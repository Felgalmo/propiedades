# -*- coding: utf-8 -*-
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import CoolProp.CoolProp as CP
import pandas as pd
import math
import logging

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app, resources={r"/*": {"origins": "*"}})

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

try:
    df_refrigerants = pd.read_csv('refrigerants.csv', encoding='utf-8-sig')
    logger.info("Columnas en refrigerants.csv: %s", df_refrigerants.columns.tolist())
except FileNotFoundError:
    df_refrigerants = pd.DataFrame()
    logger.error("refrigerants.csv no encontrado")
except Exception as e:
    df_refrigerants = pd.DataFrame()
    logger.error("Error al leer refrigerants.csv: %s", str(e), exc_info=True)

try:
    df_capillary = pd.read_csv('capillary_constants.csv', encoding='utf-8-sig')
    logger.info("Columnas en capillary_constants.csv: %s", df_capillary.columns.tolist())
except FileNotFoundError:
    df_capillary = pd.DataFrame(columns=['Refrigerant', 'C'])
    logger.error("capillary_constants.csv no encontrado")
except Exception as e:
    df_capillary = pd.DataFrame(columns=['Refrigerant', 'C'])
    logger.error("Error al leer capillary_constants.csv: %s", str(e), exc_info=True)

custom_refrigerants = ['R-454B', 'R-417A', 'R-454C', 'R-450A', 'R-452A']

COMMERCIAL_DIAMETERS = [
    0.0007112, 0.0007874, 0.0008382, 0.0009144, 0.0009906, 0.0010668,
    0.0011176, 0.0011938, 0.0012446, 0.0013208, 0.001397, 0.0014986,
    0.0016256, 0.001778
]

def interpolate(x, x0, x1, y0, y1):
    try:
        if x0 == x1:
            logger.warning("Interpolación: x0 == x1 (%s), retornando y0", x0)
            return y0
        return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    except Exception as e:
        logger.error("Error en interpolación: %s", str(e), exc_info=True)
        raise

def get_properties_from_csv(refrigerant, temp_c):
    logger.debug("Obteniendo propiedades de %s a %s°C", refrigerant, temp_c)
    if df_refrigerants.empty:
        raise ValueError("Archivo refrigerants.csv no cargado o vacío")
    
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
    
    temps = df_ref[temp_col].astype(float).tolist()
    if not temps:
        raise ValueError(f"No hay datos de temperatura para {refrigerant}")
    
    temp_lower = max([t for t in temps if t <= temp_c], default=min(temps))
    temp_upper = min([t for t in temps if t >= temp_c], default=max(temps))
    
    logger.debug("Temperaturas: lower=%s, upper=%s, target=%s", temp_lower, temp_upper, temp_c)
    
    if temp_lower == temp_upper or temp_c < min(temps) or temp_c > max(temps):
        row = df_ref[df_ref[temp_col] == temp_lower].iloc[0]
        return {
            'pressure_bubble': float(row.get('Presión Burbuja (bar)', 0)) * 100000,
            'pressure_dew': float(row.get('Presión Rocío (bar)', 0)) * 100000,
            'h_liquid': float(row.get('Entalpía Líquido (kJ/kg)', 0)) * 1000,
            'h_vapor': float(row.get('Entalpía Vapor (kJ/kg)', 0)) * 1000,
            's_liquid': float(row.get('Entropía Líquido (kJ/kg·K)', 0)) * 1000,
            's_vapor': float(row.get('Entropía Vapor (kJ/kg·K)', 0)) * 1000,
            'cp_vapor': float(row.get('Cp Vapor (kJ/kg·K)', 0)) * 1000,
            'density_liquid': float(row.get('Densidad Líquido (kg/m³)', 1200)),
            'density_vapor': float(row.get('Densidad Vapor (kg/m³)', 50))
        }
    
    row_lower = df_ref[df_ref[temp_col] == temp_lower].iloc[0]
    row_upper = df_ref[df_ref[temp_col] == temp_upper].iloc[0]
    
    props = {}
    for key in ['Presión Burbuja (bar)', 'Presión Rocío (bar)', 'Entalpía Líquido (kJ/kg)', 
                'Entalpía Vapor (kJ/kg)', 'Entropía Líquido (kJ/kg·K)', 'Entropía Vapor (kJ/kg·K)', 
                'Cp Vapor (kJ/kg·K)', 'Densidad Líquido (kg/m³)', 'Densidad Vapor (kg/m³)']:
        try:
            y0 = float(row_lower.get(key, 1200 if key == 'Densidad Líquido (kg/m³)' else 50 if key == 'Densidad Vapor (kg/m³)' else 0))
            y1 = float(row_upper.get(key, 1200 if key == 'Densidad Líquido (kg/m³)' else 50 if key == 'Densidad Vapor (kg/m³)' else 0))
            props[key] = interpolate(temp_c, temp_lower, temp_upper, y0, y1)
        except ValueError as e:
            logger.error("Error convirtiendo %s para %s: %s", key, refrigerant, str(e))
            props[key] = 1200 if key == 'Densidad Líquido (kg/m³)' else 50 if key == 'Densidad Vapor (kg/m³)' else 0
    
    return {
        'pressure_bubble': props['Presión Burbuja (bar)'] * 100000,
        'pressure_dew': props['Presión Rocío (bar)'] * 100000,
        'h_liquid': props['Entalpía Líquido (kJ/kg)'] * 1000,
        'h_vapor': props['Entalpía Vapor (kJ/kg)'] * 1000,
        's_liquid': props['Entropía Líquido (kJ/kg·K)'] * 1000,
        's_vapor': props['Entropía Vapor (kJ/kg·K)'] * 1000,
        'cp_vapor': props['Cp Vapor (kJ/kg·K)'] * 1000,
        'density_liquid': props['Densidad Líquido (kg/m³)'],
        'density_vapor': props['Densidad Vapor (kg/m³)']
    }

def get_capillary_constant(refrigerant):
    default_c = 4
    if df_capillary.empty:
        logger.warning("Capillary constants CSV vacío, usando valor por defecto: %s", default_c)
        return default_c
    possible_columns = [col for col in df_capillary.columns if col.strip().lower() in ['refrigerante', 'refrigerant']]
    if not possible_columns:
        logger.warning("Columna 'Refrigerant' no encontrada en capillary_constants.csv, usando valor por defecto: %s", default_c)
        return default_c
    refrigerant_col = possible_columns[0]
    row = df_capillary[df_capillary[refrigerant_col] == refrigerant]
    if row.empty:
        logger.warning("No se encontró constante para %s, usando valor por defecto: %s", refrigerant, default_c)
        return default_c
    try:
        return float(row['C'].iloc[0])
    except (ValueError, TypeError) as e:
        logger.error("Error al leer constante C para %s: %s", refrigerant, str(e))
        return default_c

def calculate_capillary_lengths(refrigerant, cooling_power, p1, p4, h1, h2, subcooling):
    logger.debug("Calculando longitudes de capilar para %s", refrigerant)
    is_custom = refrigerant in custom_refrigerants
    cooling_power_watts = cooling_power['value']
    if cooling_power['unit'] == 'Btu/h':
        cooling_power_watts *= 0.293071
    elif cooling_power['unit'] == 'kcal/h':
        cooling_power_watts *= 1.163

    if abs(h2 - h1) < 1e-6:
        logger.error("Diferencia de entalpía h2 - h1 es demasiado pequeña: %s", h2 - h1)
        raise ValueError("Diferencia de entalpía h2 - h1 es demasiado pequeña")
    m_dot = cooling_power_watts / (h2 - h1)
    logger.debug("Caudal másico: %s kg/s", m_dot)

    if is_custom:
        props = get_properties_from_csv(refrigerant, p4['temperature'] - 273.15)
        rho = props['density_liquid']
    else:
        try:
            if subcooling == 0:
                rho = CP.PropsSI('D', 'T', p4['temperature'], 'Q', 0, refrigerant)
            else:
                rho = CP.PropsSI('D', 'T', p4['temperature'], 'P', p4['pressure'], refrigerant)
        except ValueError as e:
            logger.warning("CoolProp density calculation failed for P4: %s. Using fallback density.", str(e))
            rho = 1200
    logger.debug("Densidad P4: %s kg/m³", rho)

    delta_p = p4['pressure'] - p1['pressure']
    if delta_p <= 0:
        logger.error("Delta P no positivo: %s", delta_p)
        raise ValueError("Delta P debe ser positivo")
    logger.debug("Diferencia de presión: %s Pa", delta_p)

    C = get_capillary_constant(refrigerant)
    logger.debug("Constante capilar C: %s", C)

    capillary_lengths = []
    for D in COMMERCIAL_DIAMETERS:
        try:
            # New capillary length equation
            numerator = delta_p * rho * (D ** 4) * C
            denominator = m_dot
            if abs(denominator) < 1e-10:
                logger.warning("Flujo másico demasiado pequeño para diámetro %s: %s", D, denominator)
                length = float('inf')
            else:
                length = numerator / denominator
            logger.debug("Diámetro: %s m, Numerador: %s, Denominador: %s, Longitud: %s m",
                         D, numerator, denominator, length)
            capillary_lengths.append({
                'diameter_mm': D * 1000,
                'length_m': round(length, 3) if length < 100 else 'N/A'
            })
        except Exception as e:
            logger.error("Error calculando longitud para diámetro %s: %s", D, str(e), exc_info=True)
            capillary_lengths.append({
                'diameter_mm': D * 1000,
                'length_m': f"Error: {str(e)}"
            })

    return capillary_lengths, m_dot

@app.route('/refrigerants', methods=['GET'])
def get_refrigerants():
    logger.info("Request received for /refrigerants")
    try:
        refrigerants = CP.FluidsList() + custom_refrigerants
        logger.debug("Refrigerantes soportados: %s", refrigerants)
        return jsonify({'status': 'success', 'refrigerants': refrigerants})
    except Exception as e:
        logger.error("Error en /refrigerants: %s", str(e), exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/thermo', methods=['POST'])
def get_thermo_properties():
    data = request.get_json()
    logger.info("Request received for /thermo with data: %s", data)
    
    try:
        refrigerant = data.get('refrigerant', 'R134a')
        evap_temp = float(data.get('evap_temp', 243.15))
        cond_temp = float(data.get('cond_temp', 313.15))
        superheat = float(data.get('superheat', 0))
        subcooling = float(data.get('subcooling', 0))
        cooling_power = data.get('cooling_power', {'value': 1000, 'unit': 'W'})
        logger.debug("Parámetros: refrigerant=%s, evap_temp=%s, cond_temp=%s, superheat=%s, subcooling=%s, cooling_power=%s",
                     refrigerant, evap_temp, cond_temp, superheat, subcooling, cooling_power)
    except (TypeError, ValueError) as e:
        logger.error("Datos de entrada inválidos: %s", str(e), exc_info=True)
        return jsonify({'status': 'error', 'message': 'Datos de entrada inválidos'}), 400

    if cond_temp <= evap_temp:
        logger.error("Temperatura de condensación menor o igual a la de evaporación: cond_temp=%s, evap_temp=%s", cond_temp, evap_temp)
        return jsonify({'status': 'error', 'message': 'La temperatura de condensación debe ser mayor que la de evaporación'}), 400
    if superheat < 0 or subcooling < 0:
        logger.error("Sobrecalentamiento o subenfriamiento negativo: superheat=%s, subcooling=%s", superheat, subcooling)
        return jsonify({'status': 'error', 'message': 'El sobrecalentamiento y subenfriamiento no pueden ser negativos'}), 400
    if cooling_power['value'] <= 0:
        logger.error("Potencia de enfriamiento inválida: %s", cooling_power['value'])
        return jsonify({'status': 'error', 'message': 'La potencia de enfriamiento debe ser mayor que cero'}), 400

    is_custom = refrigerant in custom_refrigerants
    evap_temp_c = evap_temp - 273.15
    cond_temp_c = cond_temp - 273.15

    try:
        if is_custom:
            logger.debug("Procesando refrigerante personalizado: %s", refrigerant)
            evap_props = get_properties_from_csv(refrigerant, evap_temp_c)
            cond_props = get_properties_from_csv(refrigerant, cond_temp_c)

            p4_pressure = cond_props['pressure_bubble']
            if subcooling == 0:
                p4_enthalpy = cond_props['h_liquid']
                p4_temp = cond_temp
                p4_density = cond_props['density_liquid']
            else:
                p4_temp = cond_temp - subcooling
                temp_sub = cond_temp_c - subcooling
                sub_props = get_properties_from_csv(refrigerant, temp_sub)
                p4_enthalpy = sub_props['h_liquid']
                p4_density = sub_props['density_liquid']

            p1_pressure = evap_props['pressure_dew']
            p1_enthalpy = p4_enthalpy
            p1_temp = evap_temp
            p1_density = evap_props['density_liquid']

            p2_pressure = p1_pressure
            if superheat == 0:
                p2_enthalpy = evap_props['h_vapor']
                p2_temp = evap_temp
                p2_density = evap_props['density_vapor']
            else:
                p2_temp = evap_temp + superheat
                temp_sh = evap_temp_c + superheat
                sh_props = get_properties_from_csv(refrigerant, temp_sh)
                p2_enthalpy = sh_props['h_vapor']
                p2_density = sh_props['density_vapor']
            s2 = evap_props['s_vapor']

            p3_pressure = p4_pressure
            t_evap_k = evap_temp
            t_cond_k = cond_temp
            cop_ideal = t_evap_k / (t_cond_k - t_evap_k)
            cop_real = cop_ideal * 0.75
            p3_enthalpy = p2_enthalpy + (p2_enthalpy - p4_enthalpy) / cop_real

            h_g_cond = cond_props['h_vapor']
            delta_h_superheat = p3_enthalpy - h_g_cond
            cp_vapor = cond_props['cp_vapor'] or 1000
            delta_t_superheat = delta_h_superheat / cp_vapor
            p3_temp = cond_temp + delta_t_superheat
            p3_density = cond_props['density_vapor']

            q_evap = p2_enthalpy - p1_enthalpy
            w_comp = p3_enthalpy - p2_enthalpy
            cop = q_evap / w_comp if w_comp != 0 else 0
            logger.debug("COP calculado: %s, q_evap=%s, w_comp=%s", cop, q_evap, w_comp)

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
                if temp < min(df_ref[temp_col].astype(float)) or temp > max(df_ref[temp_col].astype(float)):
                    logger.debug("Temperatura fuera de rango para saturación: %s", temp)
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
            logger.debug("Procesando refrigerante CoolProp: %s", refrigerant)
            t_min = CP.PropsSI('Tmin', refrigerant)
            t_max = CP.PropsSI('Tcrit', refrigerant)
            logger.debug("Rango de temperatura para %s: [%s°C, %s°C]", refrigerant, t_min-273.15, t_max-273.15)
            if evap_temp < t_min or cond_temp > t_max:
                raise ValueError(f"Temperatura fuera de rango para {refrigerant}: [{t_min-273.15}°C, {t_max-273.15}°C]")

            p4_pressure = CP.PropsSI('P', 'T', cond_temp, 'Q', 1, refrigerant)
            logger.debug("P4: pressure=%s Pa", p4_pressure)
            if subcooling == 0:
                p4_enthalpy = CP.PropsSI('H', 'T', cond_temp, 'Q', 0, refrigerant)
                p4_temp = cond_temp
                p4_density = CP.PropsSI('D', 'T', cond_temp, 'Q', 0, refrigerant)
            else:
                p4_temp = cond_temp - subcooling
                p4_enthalpy = CP.PropsSI('H', 'T', p4_temp, 'P', p4_pressure, refrigerant)
                p4_density = CP.PropsSI('D', 'T', p4_temp, 'P', p4_pressure, refrigerant)
            logger.debug("P4: temp=%s K, enthalpy=%s J/kg, density=%s kg/m³", p4_temp, p4_enthalpy, p4_density)

            p1_pressure = CP.PropsSI('P', 'T', evap_temp, 'Q', 0, refrigerant)
            p1_enthalpy = p4_enthalpy
            p1_temp = evap_temp
            try:
                p1_density = CP.PropsSI('D', 'T', p1_temp, 'P', p1_pressure, refrigerant)
            except ValueError as e:
                logger.warning("CoolProp density calculation failed for P1: %s. Using fallback density.", str(e))
                p1_density = 1200
            logger.debug("P1: pressure=%s Pa, enthalpy=%s J/kg, temp=%s K, density=%s kg/m³", p1_pressure, p1_enthalpy, p1_temp, p1_density)

            p2_pressure = p1_pressure
            if superheat == 0:
                p2_enthalpy = CP.PropsSI('H', 'T', evap_temp, 'Q', 1, refrigerant)
                p2_temp = evap_temp
                p2_density = CP.PropsSI('D', 'T', evap_temp, 'Q', 1, refrigerant)
            else:
                p2_temp = evap_temp + superheat
                p2_enthalpy = CP.PropsSI('H', 'T', p2_temp, 'P', p2_pressure, refrigerant)
                p2_density = CP.PropsSI('D', 'T', p2_temp, 'P', p2_pressure, refrigerant)
            s2 = CP.PropsSI('S', 'H', p2_enthalpy, 'P', p2_pressure, refrigerant)
            logger.debug("P2: pressure=%s Pa, enthalpy=%s J/kg, temp=%s K, density=%s kg/m³, entropy=%s J/kg·K", 
                         p2_pressure, p2_enthalpy, p2_temp, p2_density, s2)

            p3_pressure = p4_pressure
            try:
                p3_enthalpy = CP.PropsSI('H', 'P', p3_pressure, 'S', s2, refrigerant)
                p3_temp = CP.PropsSI('T', 'P', p3_pressure, 'H', p3_enthalpy, refrigerant)
                p3_density = CP.PropsSI('D', 'P', p3_pressure, 'H', p3_enthalpy, refrigerant)
            except ValueError as e:
                logger.warning("Cálculo isentrópico fallido: %s. Usando aproximación.", str(e))
                t_evap_k = evap_temp
                t_cond_k = cond_temp
                cop_ideal = t_evap_k / (t_cond_k - t_evap_k)
                cop_real = cop_ideal * 0.75
                p3_enthalpy = p2_enthalpy + (p2_enthalpy - p4_enthalpy) / cop_real
                p3_temp = cond_temp + 10
                p3_density = CP.PropsSI('D', 'T', p3_temp, 'P', p3_pressure, refrigerant)
            logger.debug("P3: pressure=%s Pa, enthalpy=%s J/kg, temp=%s K, density=%s kg/m³", 
                         p3_pressure, p3_enthalpy, p3_temp, p3_density)

            q_evap = p2_enthalpy - p1_enthalpy
            w_comp = p3_enthalpy - p2_enthalpy
            cop = q_evap / w_comp if w_comp != 0 else 0
            logger.debug("COP calculado: %s, q_evap=%s, w_comp=%s", cop, q_evap, w_comp)

            num_points = 50
            temp_range = (cond_temp - evap_temp) * 1.5
            temp_min = evap_temp - temp_range * 0.25
            temp_max = cond_temp + temp_range * 0.25
            temp_step = (temp_max - temp_min) / (num_points - 1)
            saturation_data = {'liquid': [], 'vapor': []}
            for i in range(num_points):
                temp = temp_min + i * temp_step
                if temp < t_min or temp > t_max:
                    logger.debug("Temperatura fuera de rango para saturación: %s K", temp)
                    continue
                p_liquid = CP.PropsSI('P', 'T', temp, 'Q', 0, refrigerant)
                h_liquid = CP.PropsSI('H', 'T', temp, 'Q', 0, refrigerant)
                p_vapor = CP.PropsSI('P', 'T', temp, 'Q', 1, refrigerant)
                h_vapor = CP.PropsSI('H', 'T', temp, 'Q', 1, refrigerant)
                saturation_data['liquid'].append({'temperature': temp - 273.15, 'pressure': p_liquid, 'enthalpy': h_liquid})
                saturation_data['vapor'].append({'temperature': temp - 273.15, 'pressure': p_vapor, 'enthalpy': h_vapor})

        p1 = {'pressure': p1_pressure, 'enthalpy': p1_enthalpy, 'temperature': p1_temp, 'density': p1_density}
        p2 = {'pressure': p2_pressure, 'enthalpy': p2_enthalpy, 'temperature': p2_temp, 'density': p2_density}
        p3 = {'pressure': p3_pressure, 'enthalpy': p3_enthalpy, 'temperature': p3_temp, 'density': p3_density}
        p4 = {'pressure': p4_pressure, 'enthalpy': p4_enthalpy, 'temperature': p4_temp, 'density': p4_density}

        logger.debug("Calculando longitudes de capilar")
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
                '1': p1,
                '2': p2,
                '3': p3,
                '4': p4
            },
            'saturation': saturation_data,
            'capillary_lengths': capillary_lengths
        }
        logger.debug("Respuesta enviada al frontend: %s", response)
        return jsonify(response)

    except Exception as e:
        logger.error("Error en cálculo termo: %s", str(e), exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/')
def serve_index():
    logger.info("Sirviendo index.html")
    return send_from_directory('.', 'index.html')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
