# -*- coding: utf-8 -*-
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import CoolProp.CoolProp as CP
import pandas as pd

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# Cargar base de datos CSV al iniciar la aplicación
custom_refrigerants = ['R-454B', 'R-417A', 'R-454C', 'R-450A', 'R-452A']
refrigerant_data = pd.read_csv('refrigerants.csv')

def interpolate_property(df, temp, prop):
    """Interpolación lineal de una propiedad en función de la temperatura."""
    df_sorted = df.sort_values('Temperature (K)')
    temps = df_sorted['Temperature (K)'].values
    props = df_sorted[prop].values
    if temp <= temps[0]:
        return props[0]
    if temp >= temps[-1]:
        return props[-1]
    for i in range(len(temps) - 1):
        if temps[i] <= temp <= temps[i + 1]:
            t0, t1 = temps[i], temps[i + 1]
            p0, p1 = props[i], props[i + 1]
            return p0 + (p1 - p0) * (temp - t0) / (t1 - t0)
    return None

@app.route('/refrigerants', methods=['GET'])
def get_refrigerants():
    try:
        coolprop_refrigerants = CP.FluidsList()
        all_refrigerants = coolprop_refrigerants + custom_refrigerants
        return jsonify({'status': 'success', 'refrigerants': sorted(all_refrigerants)})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/thermo', methods=['POST'])
def get_thermo_properties():
    data = request.get_json()
    refrigerant = data.get('refrigerant', 'R134a')
    evap_temp = float(data.get('evap_temp', 243.15))
    cond_temp = float(data.get('cond_temp', 313.15))
    superheat = float(data.get('superheat', 0))
    subcooling = float(data.get('subcooling', 0))

    try:
        is_custom = refrigerant in custom_refrigerants
        if is_custom:
            df = refrigerant_data[refrigerant_data['Refrigerant'] == refrigerant]

            # Punto 4: Salida del condensador
            p4_pressure = interpolate_property(df, cond_temp, 'Pressure (Pa)')
            if subcooling == 0:
                p4_enthalpy = interpolate_property(df, cond_temp, 'h_liquid (J/kg)')
                p4_temp = cond_temp
            else:
                p4_temp = cond_temp - subcooling
                p4_enthalpy = interpolate_property(df, p4_temp, 'h_liquid (J/kg)')

            # Punto 1: Entrada al evaporador (isoentálpico)
            p1_pressure = interpolate_property(df, evap_temp, 'Pressure (Pa)')
            p1_enthalpy = p4_enthalpy
            p1_temp = evap_temp

            # Punto 2: Salida del evaporador
            p2_pressure = p1_pressure
            if superheat == 0:
                p2_enthalpy = interpolate_property(df, evap_temp, 'h_vapor (J/kg)')
                p2_temp = evap_temp
            else:
                p2_temp = evap_temp + superheat
                p2_enthalpy = interpolate_property(df, p2_temp, 'h_vapor (J/kg)')
            
            # Cálculo de COP ajustado
            t_evap_k = evap_temp
            t_cond_k = cond_temp
            cop_ideal = t_evap_k / (t_cond_k - t_evap_k)
            cop_real = cop_ideal * 0.75
            p3_enthalpy = p2_enthalpy + (p2_enthalpy - p4_enthalpy) / cop_real

            # Cálculo de t3 ajustado para refrigerantes del CSV
            h_g_cond = interpolate_property(df, cond_temp, 'h_vapor (J/kg)')
            cp_vapor = interpolate_property(df, cond_temp, 'cp_vapor (J/kg·K)')
            delta_h_sobrecalentamiento = p3_enthalpy - h_g_cond
            delta_t_sobrecalentamiento = delta_h_sobrecalentamiento / cp_vapor
            p3_temp = cond_temp + delta_t_sobrecalentamiento
            p3_pressure = p4_pressure

            # Datos de saturación para la campana
            saturation_data = {'liquid': [], 'vapor': []}
            temps = df['Temperature (K)'].values
            temp_min, temp_max = min(temps), max(temps)
            num_points = 50
            temp_step = (temp_max - temp_min) / (num_points - 1)
            for i in range(num_points):
                temp = temp_min + i * temp_step
                p_liquid = interpolate_property(df, temp, 'Pressure (Pa)')
                h_liquid = interpolate_property(df, temp, 'h_liquid (J/kg)')
                p_vapor = p_liquid  # Asumimos misma presión para simplicidad
                h_vapor = interpolate_property(df, temp, 'h_vapor (J/kg)')
                saturation_data['liquid'].append({'temperature': temp - 273.15, 'pressure': p_liquid, 'enthalpy': h_liquid})
                saturation_data['vapor'].append({'temperature': temp - 273.15, 'pressure': p_vapor, 'enthalpy': h_vapor})

        else:
            # Usar CoolProp para refrigerantes estándar
            t_min = CP.PropsSI('Tmin', refrigerant)
            t_max = CP.PropsSI('Tcrit', refrigerant)
            if evap_temp < t_min or cond_temp > t_max:
                raise ValueError(f"Temperatura fuera de rango para {refrigerant}: [{t_min} K, {t_max} K]")

            # Punto 4
            p4_pressure = CP.PropsSI('P', 'T', cond_temp, 'Q', 1, refrigerant)
            if subcooling == 0:
                p4_enthalpy = CP.PropsSI('H', 'T', cond_temp, 'Q', 0, refrigerant)
                p4_temp = cond_temp
            else:
                p4_temp = cond_temp - subcooling
                p4_enthalpy = CP.PropsSI('H', 'T', p4_temp, 'P', p4_pressure, refrigerant)

            # Punto 1
            p1_pressure = CP.PropsSI('P', 'T', evap_temp, 'Q', 0, refrigerant)
            p1_enthalpy = p4_enthalpy
            p1_temp = evap_temp

            # Punto 2
            p2_pressure = p1_pressure
            if superheat == 0:
                p2_enthalpy = CP.PropsSI('H', 'T', evap_temp, 'Q', 1, refrigerant)
                p2_temp = evap_temp
            else:
                p2_temp = evap_temp + superheat
                p2_enthalpy = CP.PropsSI('H', 'T', p2_temp, 'P', p2_pressure, refrigerant)

            # Punto 3 con COP ajustado
            t_evap_k = evap_temp
            t_cond_k = cond_temp
            cop_ideal = t_evap_k / (t_cond_k - t_evap_k)
            cop_real = cop_ideal * 0.75
            p3_enthalpy = p2_enthalpy + (p2_enthalpy - p4_enthalpy) / cop_real
            p3_pressure = p4_pressure
            p3_temp = CP.PropsSI('T', 'P', p3_pressure, 'H', p3_enthalpy, refrigerant)

            # Datos de saturación
            num_points = 50
            temp_range = (cond_temp - evap_temp) * 1.5
            temp_min = evap_temp - temp_range * 0.25
            temp_max = cond_temp + temp_range * 0.25
            temp_step = (temp_max - temp_min) / (num_points - 1)
            saturation_data = {'liquid': [], 'vapor': []}
            for i in range(num_points):
                temp = temp_min + i * temp_step
                if temp < t_min or temp > t_max:
                    continue
                p_liquid = CP.PropsSI('P', 'T', temp, 'Q', 0, refrigerant)
                h_liquid = CP.PropsSI('H', 'T', temp, 'Q', 0, refrigerant)
                p_vapor = CP.PropsSI('P', 'T', temp, 'Q', 1, refrigerant)
                h_vapor = CP.PropsSI('H', 'T', temp, 'Q', 1, refrigerant)
                saturation_data['liquid'].append({'temperature': temp - 273.15, 'pressure': p_liquid, 'enthalpy': h_liquid})
                saturation_data['vapor'].append({'temperature': temp - 273.15, 'pressure': p_vapor, 'enthalpy': h_vapor})

        # Cálculo del COP
        q_evap = p2_enthalpy - p1_enthalpy
        w_comp = p3_enthalpy - p2_enthalpy
        cop = q_evap / w_comp if w_comp != 0 else 0

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
        return jsonify(response)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
