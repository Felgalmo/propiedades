from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import CoolProp.CoolProp as CP
import pandas as pd

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# Refrigerantes personalizados
custom_refrigerants = ['R-454B', 'R-417A', 'R-454C', 'R-450A', 'R-452A']
refrigerant_data = pd.read_csv('refrigerants.csv')

# Convertir unidades al cargar el CSV
refrigerant_data['Pressure Bubble (Pa)'] = refrigerant_data['Presión Burbuja (bar)'] * 100000  # bar a Pa
refrigerant_data['Pressure Dew (Pa)'] = refrigerant_data['Presión Rocío (bar)'] * 100000  # bar a Pa
refrigerant_data['Enthalpy Liquid (J/kg)'] = refrigerant_data['Entalpía Líquido (kJ/kg)'] * 1000  # kJ/kg a J/kg
refrigerant_data['Enthalpy Vapor (J/kg)'] = refrigerant_data['Entalpía Vapor (kJ/kg)'] * 1000  # kJ/kg a J/kg

@app.route('/refrigerants', methods=['GET'])
def get_refrigerants():
    try:
        coolprop_refrigerants = CP.FluidsList()
        all_refrigerants = coolprop_refrigerants + custom_refrigerants
        return jsonify({'status': 'success', 'refrigerants': sorted(all_refrigerants)})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

def interpolate_property(df, temp, property_col):
    """Interpolación lineal para una propiedad en una temperatura dada."""
    df_sorted = df.sort_values('Temperatura (°C)')
    temps = df_sorted['Temperatura (°C)'].values
    props = df_sorted[property_col].values
    
    if temp <= temps[0]:
        return props[0]
    if temp >= temps[-1]:
        return props[-1]
    
    for i in range(len(temps) - 1):
        if temps[i] <= temp <= temps[i + 1]:
            t0, t1 = temps[i], temps[i + 1]
            p0, p1 = props[i], props[i + 1]
            return p0 + (p1 - p0) * (temp - t0) / (t1 - t0)
    return props[-1]  # Fallback

@app.route('/thermo', methods=['POST'])
def get_thermo_properties():
    data = request.get_json()
    refrigerant = data.get('refrigerant', 'R134a')
    evap_temp = float(data.get('evap_temp', 263.15))  # K
    cond_temp = float(data.get('cond_temp', 313.15))  # K
    superheat = float(data.get('superheat', 0))
    subcooling = float(data.get('subcooling', 0))

    try:
        if refrigerant in custom_refrigerants:
            # Usar datos del CSV para refrigerantes personalizados
            df = refrigerant_data[refrigerant_data['Refrigerante'] == refrigerant]
            
            # Convertir temperaturas de K a °C
            evap_temp_c = evap_temp - 273.15
            cond_temp_c = cond_temp - 273.15
            p2_temp_c = evap_temp_c + superheat
            p4_temp_c = cond_temp_c - subcooling

            # Interpolar propiedades
            p1_pressure = interpolate_property(df, evap_temp_c, 'Pressure Dew (Pa)')  # Vapor saturado en evaporador
            p2_pressure = p1_pressure  # P2 = P1
            p3_pressure = interpolate_property(df, cond_temp_c, 'Pressure Bubble (Pa)')  # Líquido saturado en condensador
            p4_pressure = p3_pressure  # P4 = P3

            p1_enthalpy = interpolate_property(df, evap_temp_c, 'Enthalpy Liquid (J/kg)')
            p2_enthalpy = interpolate_property(df, p2_temp_c, 'Enthalpy Vapor (J/kg)')
            p4_enthalpy = interpolate_property(df, p4_temp_c, 'Enthalpy Liquid (J/kg)')

            # Calcular h3 usando el método COP
            t_evap_k = evap_temp
            t_cond_k = cond_temp
            cop_ideal = t_evap_k / (t_cond_k - t_evap_k)
            cop_real = cop_ideal * 0.75
            p3_enthalpy = p2_enthalpy + (p2_enthalpy - p4_enthalpy) / cop_real

            # Calcular t3 (temperatura de salida del compresor) usando entropía aproximada
            s2 = interpolate_property(df, p2_temp_c, 'Entropía Vapor (kJ/kg·K)') * 1000  # Convertir a J/kg·K
            h_g_cond = interpolate_property(df, cond_temp_c, 'Enthalpy Vapor (J/kg)')
            s_g_cond = interpolate_property(df, cond_temp_c, 'Entropía Vapor (kJ/kg·K)') * 1000
            # Aproximar Cp como constante (usamos una estimación basada en diferencias de entalpía y entropía)
            cp_vapor_approx = (h_g_cond - p4_enthalpy) / (cond_temp - (cond_temp - 5))  # Aproximación burda
            delta_h_overheat = p3_enthalpy - h_g_cond
            delta_t_overheat = delta_h_overheat / cp_vapor_approx
            p3_temp = cond_temp + delta_t_overheat

            # Datos de saturación para el diagrama P-h
            saturation_data = {'liquid': [], 'vapor': []}
            temp_range = df['Temperatura (°C)'].values
            for t in temp_range:
                p_liquid = interpolate_property(df, t, 'Pressure Bubble (Pa)')
                h_liquid = interpolate_property(df, t, 'Enthalpy Liquid (J/kg)')
                p_vapor = interpolate_property(df, t, 'Pressure Dew (Pa)')
                h_vapor = interpolate_property(df, t, 'Enthalpy Vapor (J/kg)')
                saturation_data['liquid'].append({'temperature': t, 'pressure': p_liquid, 'enthalpy': h_liquid})
                saturation_data['vapor'].append({'temperature': t, 'pressure': p_vapor, 'enthalpy': h_vapor})

            cop = (p2_enthalpy - p1_enthalpy) / (p3_enthalpy - p2_enthalpy)

        else:
            # Usar CoolProp para otros refrigerantes
            p2_pressure = CP.PropsSI('P', 'T', evap_temp, 'Q', 1, refrigerant)
            p1_pressure = p2_pressure
            p3_pressure = CP.PropsSI('P', 'T', cond_temp, 'Q', 0, refrigerant)
            p4_pressure = p3_pressure

            p1_enthalpy = CP.PropsSI('H', 'T', evap_temp, 'Q', 0, refrigerant)
            p2_enthalpy = CP.PropsSI('H', 'T', evap_temp + superheat, 'P', p2_pressure, refrigerant)
            s2 = CP.PropsSI('S', 'T', evap_temp + superheat, 'P', p2_pressure, refrigerant)
            try:
                p3_temp = CP.PropsSI('T', 'P', p3_pressure, 'S', s2, refrigerant)
                p3_enthalpy = CP.PropsSI('H', 'T', p3_temp, 'P', p3_pressure, refrigerant)
            except:
                p3_temp = cond_temp
                p3_enthalpy = CP.PropsSI('H', 'T', p3_temp, 'P', p3_pressure, refrigerant)
            p4_enthalpy = CP.PropsSI('H', 'T', cond_temp - subcooling, 'P', p4_pressure, refrigerant)
            p1_enthalpy = p4_enthalpy  # Expansión isoentálpica
            p1_temp = CP.PropsSI('T', 'P', p1_pressure, 'H', p1_enthalpy, refrigerant)

            cop = (p2_enthalpy - p1_enthalpy) / (p3_enthalpy - p2_enthalpy)

            t_min = CP.PropsSI('Tmin', refrigerant)
            t_max = CP.PropsSI('Tcrit', refrigerant)
            num_points = 50
            temp_step = (t_max - t_min) / (num_points - 1)
            saturation_data = {'liquid': [], 'vapor': []}
            for i in range(num_points):
                temp = t_min + i * temp_step
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
                '1': {'pressure': p1_pressure, 'enthalpy': p1_enthalpy, 'temperature': p1_temp if 'p1_temp' in locals() else evap_temp},
                '2': {'pressure': p2_pressure, 'enthalpy': p2_enthalpy, 'temperature': evap_temp + superheat},
                '3': {'pressure': p3_pressure, 'enthalpy': p3_enthalpy, 'temperature': p3_temp},
                '4': {'pressure': p4_pressure, 'enthalpy': p4_enthalpy, 'temperature': cond_temp - subcooling}
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
