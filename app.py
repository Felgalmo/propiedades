# -*- coding: utf-8 -*-
from flask import Flask, jsonify, request
from flask_cors import CORS
import CoolProp.CoolProp as CP
import pandas as pd
import logging
import math

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": ["https://mundochiller.com", "https://www.mundochiller.com"]}})

# Configurar logging para depuración
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cargar la base de datos CSV al iniciar la aplicación
df_refrigerants = pd.read_csv('/root/propiedades/refrigerants.csv')
try:
    df_capillary_constants = pd.read_csv('/root/propiedades/capillary_constants.csv')
except FileNotFoundError:
    logger.warning("capillary_constants.csv not found, using default C value")
    df_capillary_constants = pd.DataFrame(columns=['Refrigerant', 'C'])

custom_refrigerants = ['R-454B', 'R-417A', 'R-454C', 'R-450A', 'R-452A']

# Lista de diámetros comerciales en pulgadas y mmm
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

# Valor por defecto para la constante C si no se encuentra en el CSV
DEFAULT_C = 0.0001

# Función para interpolar linealmente entre dos puntos
def interpolate(x, x0, x1, y0, y1):
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)

# Obtener propiedades desde el CSV con interpolación
def get_properties_from_csv(refrigerant, temp_c):
    df_ref = df_refrigerants[df_refrigerants['Refrigerante'] == refrigerant]
    temps = df_ref['Temperatura (°C)'].tolist()
    if temp_c < min(temps) or temp_c > max(temps):
        raise ValueError(f"Temperatura {temp_c}°C fuera de rango para {refrigerant}")
    
    # Encontrar los puntos más cercanos para interpolar
    temp_lower = max([t for t in temps if t <= temp_c], default=min(temps))
    temp_upper = min([t for t in temps if t >= temp_c], default=max(temps))
    
    if temp_lower == temp_upper:
        row = df_ref[df_ref['Temperatura (°C)'] == temp_lower].iloc[0]
        return {
            'pressure_bubble': row['Presión Burbuja (bar)'] * 100000,  # Convertir a Pa
            'pressure_dew': row['Presión Rocío (bar)'] * 100000,
            'h_liquid': row['Entalpía Líquido (kJ/kg)'] * 1000,       # Convertir a J/kg
            'h_vapor': row['Entalpía Vapor (kJ/kg)'] * 1000,
            's_liquid': row['Entropía Líquido (kJ/kg·K)'] * 1000,    # Convertir a J/kg·K
            's_vapor': row['Entropía Vapor (kJ/kg·K)'] * 1000,
            'cp_vapor': row['Cp Vapor (kJ/kg·K)'] * 1000,            # Convertir a J/kg·K
            'density_liquid': row.get('Densidad Líquido (kg/m³)', 1000)  # Valor por defecto si no está
        }
    
    row_lower = df_ref[df_ref['Temperatura (°C)'] == temp_lower].iloc[0]
    row_upper = df_ref[df_ref['Temperatura (°C)'] == temp_upper].iloc[0]
    
    props = {}
    for key in ['Presión Burbuja (bar)', 'Presión Rocío (bar)', 'Entalpía Líquido (kJ/kg)', 
                'Entalpía Vapor (kJ/kg)', 'Entropía Líquido (kJ/kg·K)', 'Entropía Vapor (kJ/kg·K)', 
                'Cp Vapor (kJ/kg·K)', 'Densidad Líquido (kg/m³)']:
        y0 = row_lower.get(key, 1000 if key == 'Densidad Líquido (kg/m³)' else 0)
        y1 = row_upper.get(key, 1000 if key == 'Densidad Líquido (kg/m³)' else 0)
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

# Convertir potencia a vatios según la unidad seleccionada
def convert_power_to_watts(power, unit):
    if unit == 'W':
        return power
    elif unit == 'HP':
        return power * 745.7  # 1 HP = 745.7 W
    elif unit == 'kcal/h':
        return power * 1.163  # 1 kcal/h = 1.163 W
    elif unit == 'Btu/h':
        return power * 0.2931  # 1 Btu/h = 0.2931 W
    else:
        raise ValueError(f"Unidad de potencia no soportada: {unit}")

# Obtener la constante C desde el CSV
def get_capillary_constant(refrigerant):
    try:
        df_ref = df_capillary_constants[df_capillary_constants['Refrigerant'] == refrigerant]
        if not df_ref.empty:
            return float(df_ref.iloc[0]['C'])
        else:
            logger.warning(f"No C value found for {refrigerant}, using default C={DEFAULT_C}")
            return DEFAULT_C
    except Exception as e:
        logger.error(f"Error reading C for {refrigerant}: {str(e)}, using default C={DEFAULT_C}")
        return DEFAULT_C

# Función interna para calcular propiedades termodinámicas (reutilizada por /thermo y /capillary)
def calculate_thermo_properties(refrigerant, evap_temp, cond_temp, superheat, subcooling):
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
                p4_density = cond_props['density_liquid']
            else:
                # Aproximación para subenfriamiento
                p4_temp = cond_temp - subcooling
                temp_sub = cond_temp_c - subcooling
                sub_props = get_properties_from_csv(refrigerant, temp_sub)
                p4_enthalpy = sub_props['h_liquid']
                p4_density = sub_props['density_liquid']

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
            s2 = evap_props['s_vapor']

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
            df_ref = df_refrigerants[df_refrigerants['Refrigerante'] == refrigerant]
            num_points = 50
            temp_range = (cond_temp_c - evap_temp_c) * 1.5
            temp_min = evap_temp_c - temp_range * 0.25
            temp_max = cond_temp_c + temp_range * 0.25
            temp_step = (temp_max - temp_min) / (num_points - 1)
            saturation_data = {'liquid': [], 'vapor': []}
            for i in range(num_points):
                temp = temp_min + i * temp_step
                if temp < min(df_ref['Temperatura (°C)']) or temp > max(df_ref['Temperatura (°C)']):
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
                p4_density = CP.PropsSI('D', 'T', cond_temp, 'Q', 0, refrigerant)
            else:
                p4_temp = cond_temp - subcooling
                p4_enthalpy = CP.PropsSI('H', 'T', p4_temp, 'P', p4_pressure, refrigerant)
                p4_density = CP.PropsSI('D', 'T', p4_temp, 'P', p4_pressure, refrigerant)

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
                saturation_data['liquid'].append({
                    'temperature': temp - 273.15,
                    'pressure': p_liquid,
                    'enthalpy': h_liquid
                })
                saturation_data['vapor'].append({
                    'temperature': temp - 273.15,
                    'pressure': p_vapor,
                    'enthalpy': h_vapor
                })

        return {
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
                '4': {'pressure': p4_pressure, 'enthalpy': p4_enthalpy, 'temperature': p4_temp, 'density': p4_density}
            },
            'saturation': saturation_data
        }
    except Exception as e:
        logger.error("Error en cálculo termodinámico: %s", str(e))
        return {'status': 'error', 'message': str(e)}

@app.route('/refrigerants', methods=['GET'])
def get_refrigerants():
    try:
        refrigerants = CP.FluidsList() + custom_refrigerants
        logger.info("Refrigerantes soportados: %s", refrigerants)
        return jsonify({'status': 'success', 'refrigerants': refrigerants})
    except Exception as e:
        logger.error("Error en /refrigerants: %s", str(e))
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/thermo', methods=['POST'])
def get_thermo_properties():
    data = request.get_json()
    logger.info("Datos recibidos: %s", data)
    
    refrigerant = data.get('refrigerant', 'R134a')
    evap_temp = float(data.get('evap_temp', 243.15))  # En K
    cond_temp = float(data.get('cond_temp', 313.15))  # En K
    superheat = float(data.get('superheat', 0))
    subcooling = float(data.get('subcooling', 0))

    result = calculate_thermo_properties(refrigerant, evap_temp, cond_temp, superheat, subcooling)
    if result['status'] == 'error':
        return jsonify(result), 500
    return jsonify(result)

@app.route('/capillary', methods=['POST'])
def calculate_capillary():
    data = request.get_json()
    logger.info("Datos recibidos para cálculo de tubo capilar: %s", data)
    
    try:
        refrigerant = data.get('refrigerant', 'R134a')
        evap_temp = float(data.get('evap_temp', 243.15))  # En K
        cond_temp = float(data.get('cond_temp', 313.15))  # En K
        superheat = float(data.get('superheat', 0))
        subcooling = float(data.get('subcooling', 0))
        power = float(data.get('power', 1000))  # Potencia en la unidad especificada
        power_unit = data.get('power_unit', 'W')  # Unidad: W, HP, kcal/h, Btu/h

        # Obtener propiedades termodinámicas reutilizando la lógica de /thermo
        thermo_data = calculate_thermo_properties(refrigerant, evap_temp, cond_temp, superheat, subcooling)
        if thermo_data['status'] == 'error':
            raise ValueError(thermo_data['message'])

        # Extraer propiedades necesarias
        h1 = thermo_data['points']['1']['enthalpy']  # J/kg
        h2 = thermo_data['points']['2']['enthalpy']  # J/kg
        p4_pressure = thermo_data['points']['4']['pressure']  # Pa
        p1_pressure = thermo_data['points']['1']['pressure']  # Pa
        rho = thermo_data['points']['4']['density']  # kg/m³

        # Calcular presión diferencial
        delta_p = p4_pressure - p1_pressure  # Pa

        # Calcular flujo másico: Q = m_dot * (h2 - h1)
        q_evap = h2 - h1
        power_watts = convert_power_to_watts(power, power_unit)
        m_dot = power_watts / q_evap if q_evap != 0 else 0  # kg/s

        # Obtener la constante C para el refrigerante
        C = get_capillary_constant(refrigerant)

        # Calcular longitud para cada diámetro comercial
        # m_dot = C * D^2.5 * L^(-0.5) * sqrt(rho * delta_p)
        # L = (m_dot / (C * D^2.5 * sqrt(rho * delta_p)))^2
        if m_dot <= 0 or delta_p <= 0 or rho <= 0:
            raise ValueError("Parámetros inválidos para el cálculo del tubo capilar")
        
        sqrt_term = math.sqrt(rho * delta_p)
        capillary_results = []
        for d in COMMERCIAL_DIAMETERS:
            try:
                diameter_term = d ** 2.5
                l = (m_dot / (C * diameter_term * sqrt_term)) ** 2  # metros
                if l > 0 and l < 100:  # Filtrar longitudes no realistas
                    capillary_results.append({
                        'diameter_mm': d * 1000,  # Convertir a mm
                        'length_m': l
                    })
            except Exception as e:
                logger.warning(f"Error calculating length for diameter {d*1000} mm: {str(e)}")
                continue

        response = {
            'status': 'success',
            'refrigerant': refrigerant,
            'mass_flow': m_dot * 3600,  # kg/h
            'delta_p': delta_p / 100000,  # bar
            'density': rho,  # kg/m³
            'capillary_results': capillary_results
        }
        logger.info("Respuesta enviada para tubo capilar: %s", response)
        return jsonify(response)
    except Exception as e:
        logger.error("Error en cálculo de tubo capilar: %s", str(e))
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
