# -*- coding: utf-8 -*-
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import CoolProp.CoolProp as CP
import pandas as pd
import math

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# Cargar la base de datos CSV al iniciar
df = pd.read_csv('refrigerantes.csv')
custom_refrigerants = ['R-454B', 'R-417A', 'R-454C', 'R-450A', 'R-452A']

@app.route('/refrigerants', methods=['GET'])
def get_refrigerants():
    try:
        coolprop_refrigerants = CP.FluidsList()
        refrigerants = sorted(list(set(coolprop_refrigerants + custom_refrigerants)))
        print("Refrigerantes soportados:", refrigerants[:10], "... (total:", len(refrigerants), ")")
        return jsonify({'status': 'success', 'refrigerants': refrigerants})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

def interpolate_property(df_ref, temp, property_name):
    """Interpolar una propiedad termodinámica desde el CSV"""
    temps = df_ref['Temperatura (°C)'].tolist()
    values = df_ref[property_name].tolist()
    
    for i in range(len(temps) - 1):
        if temps[i] <= temp <= temps[i + 1]:
            t0, t1 = temps[i], temps[i + 1]
            v0, v1 = values[i], values[i + 1]
            return v0 + (v1 - v0) * (temp - t0) / (t1 - t0)
    
    if temp < temps[0]:
        return values[0]
    if temp > temps[-1]:
        return values[-1]
    raise ValueError(f"No se pudo interpolar {property_name} para T={temp}°C")

@app.route('/thermo', methods=['POST'])
def get_thermo_properties():
    data = request.get_json()
    print(f"Datos recibidos: {data}")
    
    refrigerant = data.get('refrigerant', 'R134a')
    evap_temp = float(data.get('evap_temp', 243.15))  # En K
    cond_temp = float(data.get('cond_temp', 313.15))  # En K
    superheat = float(data.get('superheat', 0))
    subcooling = float(data.get('subcooling', 0))

    evap_temp_c = evap_temp - 273.15
    cond_temp_c = cond_temp - 273.15

    try:
        if refrigerant in custom_refrigerants:
            df_ref = df[df['Refrigerante'] == refrigerant]
            t_min = df_ref['Temperatura (°C)'].min()
            t_max = df_ref['Temperatura (°C)'].max()
            if evap_temp_c < t_min or cond_temp_c > t_max:
                raise ValueError(f"Temperatura fuera de rango para {refrigerant}: [{t_min} °C, {t_max} °C]")

            # Punto 4: Salida del condensador (líquido saturado o subenfriado)
            p4_pressure = interpolate_property(df_ref, cond_temp_c, 'Presión Rocío (bar)') * 1e5
            if subcooling == 0:
                p4_enthalpy = interpolate_property(df_ref, cond_temp_c, 'Entalpía Líquido (kJ/kg)') * 1e3
                p4_temp = cond_temp
            else:
                p4_temp = cond_temp - subcooling
                h_sat = interpolate_property(df_ref, cond_temp_c, 'Entalpía Líquido (kJ/kg)')
                cp_liquid = 1.5  # kJ/(kg·K)
                p4_enthalpy = (h_sat - cp_liquid * subcooling) * 1e3

            # Punto 1: Entrada al evaporador (expansión isoentálpica)
            p1_pressure = interpolate_property(df_ref, evap_temp_c, 'Presión Burbuja (bar)') * 1e5
            p1_enthalpy = p4_enthalpy
            p1_temp = evap_temp

            # Punto 2: Salida del evaporador (entrada al compresor)
            p2_pressure = p1_pressure
            if superheat == 0:
                p2_enthalpy = interpolate_property(df_ref, evap_temp_c, 'Entalpía Vapor (kJ/kg)') * 1e3
                p2_temp = evap_temp
            else:
                p2_temp = evap_temp + superheat
                h_sat_vapor = interpolate_property(df_ref, evap_temp_c, 'Entalpía Vapor (kJ/kg)')
                cp_vapor = 0.9  # kJ/(kg·K)
                p2_enthalpy = (h_sat_vapor + cp_vapor * superheat) * 1e3

            # Punto 3: Salida del compresor (nuevo cálculo)
            p3_pressure = p4_pressure

            # Paso 1: Propiedades a la entrada del compresor (P2)
            p_evap = p2_pressure  # Pa
            h_entrada = p2_enthalpy  # J/kg
            s_entrada = interpolate_property(df_ref, evap_temp_c, 'Entropía Vapor (kJ/kg·K)') * 1e3  # J/kg·K
            t_entrada = p2_temp  # K (con superheat si aplica)

            # Paso 2: Propiedades a la presión de condensación (P4)
            p_cond = p4_pressure  # Pa
            h_g_cond = interpolate_property(df_ref, cond_temp_c, 'Entalpía Vapor (kJ/kg)') * 1e3  # J/kg

            # Paso 3: Temperatura ideal de salida (compresión isentrópica)
            gamma = 1.15  # Razón de calores específicos aproximada
            exponent = (gamma - 1) / gamma  # 0.1304
            pressure_ratio = p_cond / p_evap
            t_salida_ideal = t_entrada * (pressure_ratio ** exponent)  # K

            # Paso 4: Cambio de entalpía isentrópico
            cp_vapor = 0.85 * 1e3  # J/(kg·K)
            delta_h_is = cp_vapor * (t_salida_ideal - t_entrada)  # J/kg

            # Paso 5: Ajuste con eficiencia real
            eta_is = 0.85  # Eficiencia isentrópica
            delta_h_real = delta_h_is / eta_is  # J/kg

            # Paso 6: Entalpía a la salida real
            p3_enthalpy = h_entrada + delta_h_real  # J/kg

            # Paso 7: Temperatura de salida con sobrecalentamiento
            delta_h_sobrecalentamiento = p3_enthalpy - h_g_cond  # J/kg
            delta_t_sobrecalentamiento = delta_h_sobrecalentamiento / cp_vapor  # K
            p3_temp = cond_temp + delta_t_sobrecalentamiento  # K

            # Cálculos adicionales del ciclo
            q_evap = p2_enthalpy - p1_enthalpy
            w_comp = p3_enthalpy - p2_enthalpy
            cop = q_evap / w_comp if w_comp != 0 else 0

            # Datos de saturación para la gráfica
            num_points = 50
            temp_range = (cond_temp_c - evap_temp_c) * 1.5
            temp_min = max(t_min, evap_temp_c - temp_range * 0.25)
            temp_max = min(t_max, cond_temp_c + temp_range * 0.25)
            temp_step = (temp_max - temp_min) / (num_points - 1)
            saturation_data = {'liquid': [], 'vapor': []}
            for i in range(num_points):
                temp_c = temp_min + i * temp_step
                p_liquid = interpolate_property(df_ref, temp_c, 'Presión Burbuja (bar)') * 1e5
                h_liquid = interpolate_property(df_ref, temp_c, 'Entalpía Líquido (kJ/kg)') * 1e3
                p_vapor = interpolate_property(df_ref, temp_c, 'Presión Rocío (bar)') * 1e5
                h_vapor = interpolate_property(df_ref, temp_c, 'Entalpía Vapor (kJ/kg)') * 1e3
                saturation_data['liquid'].append({'temperature': temp_c, 'pressure': p_liquid, 'enthalpy': h_liquid})
                saturation_data['vapor'].append({'temperature': temp_c, 'pressure': p_vapor, 'enthalpy': h_vapor})

        else:
            t_min = CP.PropsSI('Tmin', refrigerant)
            t_max = CP.PropsSI('Tcrit', refrigerant)
            if evap_temp < t_min or cond_temp > t_max:
                raise ValueError(f"Temperatura fuera de rango para {refrigerant}: [{t_min} K, {t_max} K]")

            p4_pressure = CP.PropsSI('P', 'T', cond_temp, 'Q', 1, refrigerant)
            if subcooling == 0:
                p4_enthalpy = CP.PropsSI('H', 'T', cond_temp, 'Q', 0, refrigerant)
                p4_temp = cond_temp
            else:
                p4_temp = cond_temp - subcooling
                p4_enthalpy = CP.PropsSI('H', 'T', p4_temp, 'P', p4_pressure, refrigerant)

            p1_pressure = CP.PropsSI('P', 'T', evap_temp, 'Q', 0, refrigerant)
            p1_enthalpy = p4_enthalpy
            p1_temp = evap_temp

            p2_pressure = p1_pressure
            if superheat == 0:
                p2_enthalpy = CP.PropsSI('H', 'T', evap_temp, 'Q', 1, refrigerant)
                p2_temp = evap_temp
            else:
                p2_temp = evap_temp + superheat
                p2_enthalpy = CP.PropsSI('H', 'T', p2_temp, 'P', p2_pressure, refrigerant)
            s2 = CP.PropsSI('S', 'H', p2_enthalpy, 'P', p2_pressure, refrigerant)

            p3_pressure = p4_pressure
            p3_enthalpy = CP.PropsSI('H', 'P', p3_pressure, 'S', s2, refrigerant)
            p3_temp = CP.PropsSI('T', 'P', p3_pressure, 'H', p3_enthalpy, refrigerant)

            q_evap = p2_enthalpy - p1_enthalpy
            w_comp = p3_enthalpy - p2_enthalpy
            cop = q_evap / w_comp if w_comp != 0 else 0

            num_points = 50
            temp_range = (cond_temp - evap_temp) * 1.5
            temp_min = max(t_min, evap_temp - temp_range * 0.25)
            temp_max = min(t_max, cond_temp + temp_range * 0.25)
            temp_step = (temp_max - temp_min) / (num_points - 1)
            saturation_data = {'liquid': [], 'vapor': []}
            for i in range(num_points):
                temp = temp_min + i * temp_step
                p_liquid = CP.PropsSI('P', 'T', temp, 'Q', 0, refrigerant)
                h_liquid = CP.PropsSI('H', 'T', temp, 'Q', 0, refrigerant)
                p_vapor = CP.PropsSI('P', 'T', temp, 'Q', 1, refrigerant)
                h_vapor = CP.PropsSI('H', 'T', temp, 'Q', 1, refrigerant)
                saturation_data['liquid'].append({'temperature': temp - 273.15, 'pressure': p_liquid, 'enthalpy': h_liquid})
                saturation_data['vapor'].append({'temperature': temp - 273.15, 'pressure': p_vapor, 'enthalpy': h_vapor})

        response = {
            'status': 'success',
            'refrigerant': refrigerant,
            'evap_temp': evap_temp,
            'cond_temp': cond_temp,
            'superheat': superheat,
            'subcooling': subcooling,
            'cop': cop,
            'points': {
                '1': {'pressure': p1_pressure, 'enthalpy': p1_enthalpy, 'temperature': p1_temp},
                '2': {'pressure': p2_pressure, 'enthalpy': p2_enthalpy, 'temperature': p2_temp},
                '3': {'pressure': p3_pressure, 'enthalpy': p3_enthalpy, 'temperature': p3_temp},
                '4': {'pressure': p4_pressure, 'enthalpy': p4_enthalpy, 'temperature': p4_temp}
            },
            'saturation': saturation_data
        }
        print("Respuesta enviada al frontend:", response)
        return jsonify(response)
    except Exception as e:
        print(f"Error en cálculo: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/')
def serve_index():
    print("Sirviendo index.html")
    return send_from_directory('.', 'index.html')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
