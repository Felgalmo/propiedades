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

            # Punto 3: Salida del compresor
            p3_pressure = p4_pressure

            # Cálculo de h3 con COP ajustado
            t_evap_k = evap_temp
            t_cond_k = cond_temp
            cop_ideal = t_evap_k / (t_cond_k - t_evap_k)
            cop_real = cop_ideal * 0.75
            p3_enthalpy = p2_enthalpy + (p2_enthalpy - p4_enthalpy) / cop_real  # h_salida

            # Cálculo de t3 usando h3 del COP
            p_evap = p2_pressure
            h_entrada = p2_enthalpy
            t_entrada = p2_temp
            p_cond = p4_pressure
            h_g_cond = interpolate_property(df_ref, cond_temp_c, 'Entalpía Vapor (kJ/kg)') * 1e3

            # Paso 3: Temperatura ideal (como referencia, pero no determina h3)
            gamma = 1.2
            exponent = (gamma - 1) / gamma  # 0.1667
            pressure_ratio = p_cond / p_evap
            t_salida_ideal = t_entrada * (pressure_ratio ** exponent)

            # Paso 4 y 5: Cambio de entalpía isentrópico y real (solo para referencia)
            cp_vapor = 0.9 * 1e3  # J/(kg·K)
            delta_h_is = cp_vapor * (t_salida_ideal - t_entrada)
            eta_is = 0.85
            delta_h_real = delta_h_is / eta_is

            # Paso 7: Temperatura de salida usando h3 del COP
            delta_h_sobrecalentamiento = p3_enthalpy - h_g_cond  # Δh = h_salida - h_g
            delta_t_sobrecalentamiento = delta_h_sobrecalentamiento / cp_vapor
            p3_temp = cond_temp + delta_t_sobrecalentamiento  # K
