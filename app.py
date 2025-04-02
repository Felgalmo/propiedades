# -*- coding: utf-8 -*-
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import CoolProp.CoolProp as CP

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

@app.route('/refrigerants', methods=['GET'])
def get_refrigerants():
    try:
        refrigerants = CP.FluidsList()
        print("Refrigerantes soportados:", refrigerants)
        return jsonify({'status': 'success', 'refrigerants': refrigerants})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/thermo', methods=['POST'])
def get_thermo_properties():
    data = request.get_json()
    print(f"Datos recibidos: {data}")
    
    refrigerant = data.get('refrigerant', 'R134a')
    evap_temp = float(data.get('evap_temp', 243.15))  # -30°C por defecto
    cond_temp = float(data.get('cond_temp', 313.15))  # 40°C por defecto
    superheat = float(data.get('superheat', 0))
    subcooling = float(data.get('subcooling', 0))

    try:
        # Verificar rangos válidos
        t_min = CP.PropsSI('Tmin', refrigerant)
        t_max = CP.PropsSI('Tcrit', refrigerant)
        if evap_temp < t_min or cond_temp > t_max:
            raise ValueError(f"Temperatura fuera de rango para {refrigerant}: [{t_min} K, {t_max} K]")

        # Punto 1: Entrada al evaporador (líquido saturado después de la expansión)
        p1_pressure = CP.PropsSI('P', 'T', cond_temp - subcooling, 'Q', 0, refrigerant)  # Presión del condensador
        p1_enthalpy = CP.PropsSI('H', 'T', cond_temp - subcooling, 'Q', 0, refrigerant)  # Entalpía líquida saturada o subenfriada
        p1_temp = evap_temp  # Temperatura después de la expansión

        # Punto 2: Salida del evaporador (vapor saturado o sobrecalentado)
        p2_pressure = CP.PropsSI('P', 'T', evap_temp, 'Q', 1, refrigerant)  # Presión de vapor saturado
        if superheat == 0:
            p2_enthalpy = CP.PropsSI('H', 'T', evap_temp, 'Q', 1, refrigerant)  # Vapor saturado
            p2_temp = evap_temp
        else:
            p2_temp = evap_temp + superheat
            p2_enthalpy = CP.PropsSI('H', 'T', p2_temp, 'P', p2_pressure, refrigerant)  # Vapor sobrecalentado
        s2 = CP.PropsSI('S', 'T', p2_temp, 'P', p2_pressure, refrigerant)

        # Punto 3: Salida del compresor (compresión isoentrópica)
        p3_pressure = CP.PropsSI('P', 'T', cond_temp, 'Q', 1, refrigerant)  # Presión del condensador
        p3_temp = CP.PropsSI('T', 'P', p3_pressure, 'S', s2, refrigerant)
        p3_enthalpy = CP.PropsSI('H', 'T', p3_temp, 'P', p3_pressure, refrigerant)

        # Punto 4: Salida del condensador (líquido saturado o subenfriado)
        p4_pressure = p3_pressure
        if subcooling == 0:
            p4_enthalpy = CP.PropsSI('H', 'T', cond_temp, 'Q', 0, refrigerant)  # Líquido saturado
            p4_temp = cond_temp
        else:
            p4_temp = cond_temp - subcooling
            p4_enthalpy = CP.PropsSI('H', 'T', p4_temp, 'P', p4_pressure, refrigerant)

        # Cálculo del COP
        q_evap = p2_enthalpy - p1_enthalpy  # Calor absorbido
        w_comp = p3_enthalpy - p2_enthalpy  # Trabajo del compresor
        cop = q_evap / w_comp if w_comp != 0 else 0

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
