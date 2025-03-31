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
        print("Refrigerantes soportados:", refrigerants)  # Para verificar en consola
        return jsonify({'status': 'success', 'refrigerants': refrigerants})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/thermo', methods=['POST'])
def get_thermo_properties():
    data = request.get_json()
    print(f"Datos recibidos: {data}")
    
    refrigerant = data.get('refrigerant', 'R134a')
    evap_temp = float(data.get('evap_temp', 263.15))  # Temperatura en K
    cond_temp = float(data.get('cond_temp', 313.15))  # Temperatura en K
    superheat_input = float(data.get('superheat', 0))  # Acepta decimales
    subcooling_input = float(data.get('subcooling', 0))  # Acepta decimales
    condenser_failure = data.get('condenser_failure', 'normal')
    evaporator_failure = data.get('evaporator_failure', 'normal')

    try:
        # Ajustar temperaturas según fallas
        cond_temp_adjusted = cond_temp
        evap_temp_adjusted = evap_temp
        superheat = superheat_input
        subcooling = subcooling_input

        # Ajustes por fallas del evaporador
        if evaporator_failure == 'low':
            evap_temp_adjusted -= 2
        elif evaporator_failure == 'medium':
            evap_temp_adjusted -= 5
            superheat = max(superheat, 2)
        elif evaporator_failure == 'severe':
            evap_temp_adjusted -= 10
            superheat = max(superheat, 5)

        # Ajustes por fallas del condensador
        if condenser_failure == 'low':
            cond_temp_adjusted += 5
            subcooling = 0  # Líquido saturado
        elif condenser_failure == 'medium':
            cond_temp_adjusted += 10
            subcooling = 0  # Mezcla dentro de la campana
        elif condenser_failure == 'severe':
            cond_temp_adjusted += 20
            subcooling = 0  # Mezcla más adentro de la campana

        # Punto 2: Salida del compresor
        p2_pressure = CP.PropsSI('P', 'T', evap_temp_adjusted, 'Q', 1, refrigerant)
        if evaporator_failure == 'severe':
            p2_pressure *= 1.05
        elif evaporator_failure == 'medium':
            p2_pressure *= 1.03
        elif evaporator_failure == 'low':
            p2_pressure *= 1.01
        
        if condenser_failure == 'severe':
            p2_pressure *= 1.05
        elif condenser_failure == 'medium':
            p2_pressure *= 1.03
        elif condenser_failure == 'low':
            p2_pressure *= 1.01

        if superheat > 0:
            p2_temp = evap_temp_adjusted + superheat
            p2_enthalpy = CP.PropsSI('H', 'T', p2_temp, 'P', p2_pressure, refrigerant)
            s2 = CP.PropsSI('S', 'T', p2_temp, 'P', p2_pressure, refrigerant)
        else:
            p2_temp = evap_temp_adjusted
            p2_enthalpy = CP.PropsSI('H', 'P', p2_pressure, 'Q', 1, refrigerant)  # Vapor saturado
            s2 = CP.PropsSI('S', 'P', p2_pressure, 'Q', 1, refrigerant)  # Entropía de vapor saturado

        # Punto 3: Entrada al condensador (compresión isoentrópica)
        p3_pressure = CP.PropsSI('P', 'T', cond_temp_adjusted, 'Q', 0, refrigerant)
        try:
            p3_temp = CP.PropsSI('T', 'P', p3_pressure, 'S', s2, refrigerant)
            p3_enthalpy = CP.PropsSI('H', 'T', p3_temp, 'P', p3_pressure, refrigerant)
        except ValueError as e:
            print(f"Error en compresión isoentrópica: {e}. Usando temperatura de condensación ajustada.")
            p3_temp = cond_temp_adjusted
            p3_enthalpy = CP.PropsSI('H', 'T', p3_temp, 'P', p3_pressure, refrigerant)

        # Punto 4: Salida del condensador (sin pérdida de presión)
        p4_pressure = p3_pressure  # Eliminada la pérdida de presión
        if condenser_failure == 'normal':
            if subcooling > 0:
                p4_temp = cond_temp_adjusted - subcooling
                p4_enthalpy = CP.PropsSI('H', 'T', p4_temp, 'P', p4_pressure, refrigerant)
            else:
                p4_temp = cond_temp_adjusted
                p4_enthalpy = CP.PropsSI('H', 'P', p4_pressure, 'Q', 0, refrigerant)  # Líquido saturado
        elif condenser_failure == 'low':
            p4_temp = cond_temp_adjusted
            p4_enthalpy = CP.PropsSI('H', 'P', p4_pressure, 'Q', 0, refrigerant)  # Líquido saturado
        elif condenser_failure == 'medium':
            p4_enthalpy = CP.PropsSI('H', 'P', p4_pressure, 'Q', 0.2, refrigerant)  # Mezcla con calidad 0.2
            p4_temp = CP.PropsSI('T', 'P', p4_pressure, 'Q', 0.2, refrigerant)
        elif condenser_failure == 'severe':
            p4_enthalpy = CP.PropsSI('H', 'P', p4_pressure, 'Q', 0.5, refrigerant)  # Mezcla con calidad 0.5
            p4_temp = CP.PropsSI('T', 'P', p4_pressure, 'Q', 0.5, refrigerant)

        # Punto 1: Entrada al evaporador (expansión isoentálpica)
        p1_pressure = p2_pressure  # Según tu instrucción: P1 = P2
        p1_enthalpy = p4_enthalpy  # Expansión isoentálpica
        try:
            p1_temp = CP.PropsSI('T', 'P', p1_pressure, 'H', p1_enthalpy, refrigerant)
        except ValueError as e:
            print(f"Error al calcular T1: {e}. Usando temperatura de evaporación ajustada como aproximación.")
            p1_temp = evap_temp_adjusted  # Fallback en caso de error

        # Cálculo del COP
        q_evap = p2_enthalpy - p1_enthalpy  # Calor extraído en el evaporador (h2 - h1)
        w_comp = p3_enthalpy - p2_enthalpy  # Energía requerida por el compresor (h3 - h2)
        cop = q_evap / w_comp if w_comp != 0 else 0  # COP = (h2 - h1) / (h3 - h2)

        # Datos de saturación para el diagrama P-h
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
            'evap_temp': evap_temp_adjusted,
            'cond_temp': cond_temp_adjusted,
            'superheat': superheat,
            'subcooling': subcooling,
            'cop': cop,
            'condenser_failure': condenser_failure,
            'evaporator_failure': evaporator_failure,
            'points': {
                '1': {'pressure': p1_pressure, 'enthalpy': p1_enthalpy, 'temperature': p1_temp},
                '2': {'pressure': p2_pressure, 'enthalpy': p2_enthalpy, 'temperature': p2_temp},
                '3': {'pressure': p3_pressure, 'enthalpy': p3_enthalpy, 'temperature': p3_temp},
                '4': {'pressure': p4_pressure, 'enthalpy': p4_enthalpy, 'temperature': p4_temp}
            },
            'saturation': saturation_data,
            'debug': {
                'p2_temp': p2_temp - 273.15,
                'p3_temp': p3_temp - 273.15,
                'p4_quality': CP.PropsSI('Q', 'P', p4_pressure, 'H', p4_enthalpy, refrigerant) if condenser_failure in ['medium', 'severe'] else None,
                's2': s2
            }
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