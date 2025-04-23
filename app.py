# -*- coding: utf-8 -*-
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import CoolProp.CoolProp as CP
import pandas as pd
import math

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# Cargar las bases de datos CSV al iniciar la aplicación
try:
    df_refrigerants = pd.read_csv('refrigerants.csv', encoding='utf-8-sig')
    print("Columnas en refrigerants.csv:", df_refrigerants.columns.tolist())
except FileNotFoundError:
    df_refrigerants = pd.DataFrame()
    print("Error: refrigerants.csv no encontrado")
except Exception as e:
    df_refrigerants = pd.DataFrame()
    print(f"Error al leer refrigerants.csv: {str(e)}")

try:
    df_capillary = pd.read_csv('capillary_constants.csv', encoding='utf-8-sig')
    print("Columnas en capillary_constants.csv:", df_capillary.columns.tolist())
except FileNotFoundError:
    df_capillary = pd.DataFrame(columns=['Refrigerante', 'C'])
    print("Error: capillary_constants.csv no encontrado")
except Exception as e:
    df_capillary = pd.DataFrame(columns=['Refrigerante', 'C'])
    print(f"Error al leer capillary_constants.csv: {str(e)}")

custom_refrigerants = ['R-454B', 'R-417A', 'R-454C', 'R-450A', 'R-452A']

# Lista de diámetros comerciales en metros
COMMERCIAL_DIAMETERS = [
    0.0007,  # 0.70 mm
    0.0008,  # 0.80 mm
    0.00085, # 0.85 mm
    0.0009,  # 0.90 mm
    0.001,   # 1.00 mm
    0.00105, # 1.05 mm
    0.0011,  # 1.10 mm
    0.0012,  # 1.20 mm
    0.00125, # 1.25 mm
    0.0013,  # 1.30 mm
    0.0014,  # 1.40 mm
    0.0015,  # 1.50 mm
    0.0016,  # 1.60 mm
    0.0018   # 1.80 mm
]

# Función para interpolar linealmente entre dos puntos
def interpolate(x, x0, x1, y0, y1):
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)

# Obtener propiedades desde el CSV con interpolación
def get_properties_from_csv(refrigerant, temp_c):
    if df_refrigerants.empty:
        raise ValueError("Archivo refrigerants.csv no cargado o vacío")
    
    # Buscar columna 'Refrigerante' (insensible a mayúsculas y espacios)
    possible_columns = [col for col in df_refrigerants.columns if col.strip().lower() in ['refrigerante', 'refrigerant']]
    if not possible_columns:
        raise ValueError("Columna 'Refrigerante' no encontrada en refrigerants.csv")
    refrigerant_col = possible_columns[0]
    
    df_ref = df_refrigerants[df_refrigerants[refrigerant_col] == refrigerant]
    if df_ref.empty:
        raise ValueError(f"Refrigerante {refrigerant} no encontrado en refrigerants.csv")
    
    temp_col = next((col for col in df_refrigerants.columns if 'Temperatura' in col), None)
    if not temp_col:
        raise ValueError("Columna de temperatura no encontrada en refrigerants.csv")
    
    temps = df_ref[temp_col].tolist()
    if not temps:
        raise ValueError(f"No hay datos de temperatura para {refrigerant}")
    if temp_c < min(temps) or temp_c > max(temps):
        raise ValueError(f"Temperatura {temp_c}°C fuera de rango para {refrigerant}")
    
    # Encontrar los puntos más cercanos para interpolar
    temp_lower = max([t for t in temps if t <= temp_c], default=min(temps))
    temp_upper = min([t for t in temps if t >= temp_c], default=max(temps))
    
    if temp_lower == temp_upper:
        row = df_ref[df_ref[temp_col] == temp_lower].iloc[0]
        return {
            'pressure_bubble': row.get('Presión Burbuja (bar)', 0) * 100000,  # Convertir a Pa
            'pressure_dew': row.get('Presión Rocío (bar)', 0) * 100000,
            'h_liquid': row.get('Entalpía Líquido (kJ/kg)', 0) * 1000,       # Convertir a J/kg
            'h_vapor': row.get('Entalpía Vapor (kJ/kg)', 0) * 1000,
            's_liquid': row.get('Entropía Líquido (kJ/kg·K)', 0) * 1000,    # Convertir a J/kg·K
            's_vapor': row.get('Entropía Vapor (kJ/kg·K)', 0) * 1000,
            'cp_vapor': row.get('Cp Vapor (kJ/kg·K)', 0) * 1000,            # Convertir a J/kg·K
            'density_liquid': row.get('Densidad Líquido (kg/m³)', 1200)     # Valor por defecto
        }
    
    row_lower = df_ref[df_ref[temp_col] == temp_lower].iloc[0]
    row_upper = df_ref[df_ref[temp_col] == temp_upper].iloc[0]
    
    props = {}
    for key in ['Presión Burbuja (bar)', 'Presión Rocío (bar)', 'Entalpía Líquido (kJ/kg)', 
                'Entalpía Vapor (kJ/kg)', 'Entropía Líquido (kJ/kg·K)', 'Entropía Vapor (kJ/kg·K)', 
                'Cp Vapor (kJ/kg·K)', 'Densidad Líquido (kg/m³)']:
        y0 = row_lower.get(key, 1200 if key == 'Densidad Líquido (kg/m³)' else 0)
        y1 = row_upper.get(key, 1200 if key == 'Densidad Líquido (kg/m³)' else 0)
        props[key] = interpolate(temp_c, temp_lower, temp_upper, y0, y1)
    
    return {
        'pressure_bubble': props['Presión Burbuja (bar)'] * 100000,
        'pressure_dew': props['Presión Rocío (bar)'] * 100000,
        'h_liquid': props['Entalpía Líquido (kJ/kg)'] * 1000,
        'h_vapor': props['Entalpía Vapor (kJ/kg)'] * 1000,
        's_liquid': props['Entropía Líquido (kJ/kg·K)'] * 1000,
        's_vapor': props['Entropía Vapor (kJ/kg·K)'] * 1000,
        'cp_vapor': props['Cp Vapor (kJ/kg·K)'] * 1000,
        'density_liquid': props['Densidad Líquido (kg/m³)']
    }

# Obtener la constante C desde el CSV
def get_capillary_constant(refrigerant):
    default_c = 0.0001
    if df_capillary.empty:
        return default_c
    possible_columns = [col for col in df_capillary.columns if col.strip().lower() in ['refrigerante', 'refrigerant']]
    if not possible_columns:
        return default_c
    refrigerant_col = possible_columns[0]
    row = df_capillary[df_capillary[refrigerant_col] == refrigerant]
    if row.empty:
        return default_c
    return row.get('C', default_c)

# Calcular longitud del tubo capilar
def calculate_capillary_lengths(refrigerant, cooling_power, p1, p4, h1, h2, subcooling):
    is_custom = refrigerant in custom_refrigerants
    # Convertir cooling_power a vatios según la unidad
    cooling_power_watts = cooling_power['value']
    if cooling_power['unit'] == 'Btu/h':
        cooling_power_watts *= 0.293071  # 1 Btu/h = 0.293071 W
    elif cooling_power['unit'] == 'kcal/h':
        cooling_power_watts *= 1.163  # 1 kcal/h = 1.163 W

    # Calcular caudal másico: m_dot = Q_cooling / (h2 - h1)
    if h2 - h1 == 0:
        raise ValueError("Diferencia de entalpía h2 - h1 es cero")
    m_dot = cooling_power_watts / (h2 - h1)  # kg/s

    # Obtener densidad en el punto 4
    if is_custom:
        props = get_properties_from_csv(refrigerant, p4['temperature'] - 273.15)
        rho = props['density_liquid']
    else:
        try:
            if subcooling == 0:
                # Use saturated liquid density to avoid saturation boundary issues
                rho = CP.PropsSI('D', 'T', p4['temperature'], 'Q', 0, refrigerant)
            else:
                # Subcooled state should be safe
                rho = CP.PropsSI('D', 'T', p4['temperature'], 'P', p4['pressure'], refrigerant)
        except ValueError as e:
            # Fallback: Use saturated liquid density with slight temperature adjustment
            print(f"CoolProp density calculation failed: {str(e)}. Using saturated liquid fallback.")
            rho = CP.PropsSI('D', 'T', p4['temperature'] - 0.1, 'Q', 0, refrigerant)

    # Calcular Delta P
    delta_p = p4['pressure'] - p1['pressure']  # Pa
    if delta_p <= 0:
        raise ValueError("Delta P debe ser positivo")

    # Obtener constante C
    C = get_capillary_constant(refrigerant)

    # Calcular longitud para cada diámetro
    capillary_lengths = []
    for D in COMMERCIAL_DIAMETERS:
        try:
            # Fórmula: L = (m_dot / (C * D^2.5 * sqrt(rho * Delta_P)))^2
            denominator = C * (D ** 2.5) * math.sqrt(rho * delta_p)
            if denominator == 0:
                length = float('inf')
            else:
                length = (m_dot / denominator) ** 2  # metros
            capillary_lengths.append({
                'diameter_mm': D * 1000,  # Convertir a mm para la respuesta
                'length_m': round(length, 2)
            })
        except Exception as e:
            capillary_lengths.append({
                'diameter_mm': D * 1000,
                'length_m': f"Error: {str(e)}"
            })

    return capillary_lengths, m_dot

@app.route('/refrigerants', methods=['GET'])
def get_refrigerants():
    try:
        refrigerants = CP.FluidsList() + custom_refrigerants
        print("Refrigerantes soportados:", refrigerants)
        return jsonify({'status': 'success', 'refrigerants': refrigerants})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/thermo', methods=['POST'])
def get_thermo_properties():
    data = request.get_json()
    print(f"Datos recibidos: {data}")
    
    refrigerant = data.get('refrigerant', 'R134a')
    evap_temp = float(data.get('evap_temp', 243.15))  # En K
    cond_temp = float(data.get('cond_temp', 313.15))  # En K
    superheat = float(data.get('superheat', 0))
    subcooling = float(data.get('subcooling', 0))
    cooling_power = data.get('cooling_power', {'value': 1000, 'unit': 'W'})  # Default: 1000 W

    is_custom = refrigerant in custom_refrigerants
    evap_temp_c = evap_temp - 273.15
    cond_temp_c = cond_temp - 273.15

    try:
        if is_custom:
            # Usar CSV para refrigerantes personalizados
            evap_props = get_properties_from_csv(refrigerant, evap_temp_c)
            cond_props = get_properties_from_csv(refrigerant, cond_temp_c)

            # Punto 4: Salida del condensador
            p4_pressure = cond_props['pressure_bubble']
            if subcooling == 0:
                p4_enthalpy = cond_props['h_liquid']
                p4_temp = cond_temp
            else:
                # Aproximación para subenfriamiento (usamos h_liquid y ajustamos linealmente)
                p4_temp = cond_temp - subcooling
                temp_sub = cond_temp_c - subcooling
                sub_props = get_properties_from_csv(refrigerant, temp_sub)
                p4_enthalpy = sub_props['h_liquid']

            # Punto 1: Entrada al evaporador (isoentálpico)
            p1_pressure = evap_props['pressure_dew']
            p1_enthalpy = p4_enthalpy
            p1_temp = evap_temp

            # Punto 2: Salida del evaporador
            p2_pressure = p1_pressure
            if superheat == 0:
                p2_enthalpy = evap_props['h_vapor']
                p2_temp = evap_temp
            else:
                p2_temp = evap_temp + superheat
                temp_sh = evap_temp_c + superheat
                sh_props = get_properties_from_csv(refrigerant, temp_sh)
                p2_enthalpy = sh_props['h_vapor']
            s2 = evap_props['s_vapor']  # Entropía en vapor saturado como base

            # Punto 3: Salida del compresor con COP ajustado
            p3_pressure = p4_pressure
            t_evap_k = evap_temp
            t_cond_k = cond_temp
            cop_ideal = t_evap_k / (t_cond_k - t_evap_k)
            cop_real = cop_ideal * 0.75
            p3_enthalpy = p2_enthalpy + (p2_enthalpy - p4_enthalpy) / cop_real

            # Cálculo de T3 ajustado para CSV
            h_g_cond = cond_props['h_vapor']
            delta_h_superheat = p3_enthalpy - h_g_cond
            cp_vapor = cond_props['cp_vapor']
            delta_t_superheat = delta_h_superheat / cp_vapor
            p3_temp = cond_temp + delta_t_superheat

            # Cálculo del COP
            q_evap = p2_enthalpy - p1_enthalpy
            w_comp = p3_enthalpy - p2_enthalpy
            cop = q_evap / w_comp if w_comp != 0 else 0

            # Datos de saturación para la campana
            possible_columns = [col for col in df_refrigerants.columns if col.strip().lower() in ['refrigerante', 'refrigerant']]
            refrigerant_col = possible_columns[0]
            temp_col = next((col for col in df_refrigerants.columns if 'Temperatura' in col), None)
            df_ref = df_refrigerants[df_refrigerants[refrigerant_col] == refrigerant]
            num_points = 50
            temp_range = (cond_temp_c - evap_temp_c) * 1.5
            temp_min = evap_temp_c - temp_range * 0.25
            temp_max = cond_temp_c + temp_range * 0.25
            temp_step = (temp_max - temp_min) / (num_points - 1)
            saturation_data = {'liquid': [], 'vapor': []}
            for i in range(num_points):
                temp = temp_min + i * temp_step
                if temp < min(df_ref[temp_col]) or temp > max(df_ref[temp_col]):
                    continue
                props = get_properties_from_csv(refrigerant, temp)
                saturation_data['liquid'].append({
                    'temperature': temp,
                    'pressure': props['pressure_bubble'],
                    'enthalpy': props['h_liquid']
                })
                saturation_data['vapor'].append({
                    'temperature': temp,
                    'pressure': props['pressure_dew'],
                    'enthalpy': props['h_vapor']
                })

        else:
            # Usar CoolProp para refrigerantes estándar
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

        # Calcular longitudes de tubo capilar
        p1 = {'pressure': p1_pressure, 'enthalpy': p1_enthalpy, 'temperature': p1_temp}
        p2 = {'pressure': p2_pressure, 'enthalpy': p2_enthalpy, 'temperature': p2_temp}
        p4 = {'pressure': p4_pressure, 'enthalpy': p4_enthalpy, 'temperature': p4_temp}
        capillary_lengths, mass_flow = calculate_capillary_lengths(
            refrigerant, cooling_power, p1, p4, p1_enthalpy, p2_enthalpy, subcooling
        )

        response = {
            'status': 'success',
            'refrigerant': refrigerant,
            'evap_temp': evap_temp,
            'cond_temp': cond_temp,
            'superheat': superheat,
            'subcooling': subcooling,
            'cop': cop,
            'mass_flow': mass_flow,
            'points': {
                '1': {'pressure': p1_pressure, 'enthalpy': p1_enthalpy, 'temperature': p1_temp},
                '2': {'pressure': p2_pressure, 'enthalpy': p2_enthalpy, 'temperature': p2_temp},
                '3': {'pressure': p3_pressure, 'enthalpy': p3_enthalpy, 'temperature': p3_temp},
                '4': {'pressure': p4_pressure, 'enthalpy': p4_enthalpy, 'temperature': p4_temp}
            },
            'saturation': saturation_data,
            'capillary_lengths': capillary_lengths
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
