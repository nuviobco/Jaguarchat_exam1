import openai
import requests 
from pymongo import MongoClient
from analisis import analizar_temas_mas_consultados
from analisis import col_historial
import os
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template, send_file, redirect, url_for, flash, session, make_response
from flask import render_template
from app import app  # Asume que la instancia de Flask se llama 'app' y está en el archivo 'app.py'

@app.route('/examen')
def examen():
    return render_template('examen.html')

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
MAILGUN_API_KEY = os.getenv("MAILGUN_API_KEY")
MAILGUN_DOMAIN = os.getenv("MAILGUN_DOMAIN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Configurar la API de OpenAI
import openai
openai.api_key = OPENAI_API_KEY

client = MongoClient("tu_mongodb_uri")
db = client["Jaguar"]
col_examenes = db["examenes"]


def generar_preguntas():
    # Obtener los temas más consultados
    prompts = col_historial.find()  # Asegúrate de importar col_historial desde tu archivo analisis.py
    temas_mas_consultados = analizar_temas_mas_consultados(prompts)

    # Utiliza la API de OpenAI para generar preguntas basadas en los temas más consultados
    preguntas = []
    for tema, _ in temas_mas_consultados:
        prompt = f"Genera una pregunta de opción múltiple sobre el tema {tema}. Incluye la pregunta y cuatro opciones de respuesta, con la respuesta correcta indicada."

        respuesta_openai = openai.Completion.create(
            engine="text-davinci-003",
            prompt=prompt,
            max_tokens=100,
            n=1,
            stop=None,
            temperature=0.5,
        )

        pregunta_generada = respuesta_openai.choices[0].text.strip()
        preguntas.append(pregunta_generada)

        if len(preguntas) >= 5:
            break

    return preguntas

def guardar_preguntas(preguntas):
    # Guarda las preguntas generadas en MongoDB
    documento_preguntas = {"preguntas": preguntas}
    col_examenes.insert_one(documento_preguntas)

def obtener_preguntas(tema):
    # Obtiene las preguntas guardadas en MongoDB basadas en el tema
    preguntas_por_tema = list(col_examenes.find({"preguntas.pregunta": {"$regex": tema, "$options": "i"}}))
    preguntas_seleccionadas = preguntas_por_tema[0]["preguntas"] if preguntas_por_tema else []
    return preguntas_seleccionadas

def revisar_respuestas(respuestas_usuario, preguntas):
    # Revisa las respuestas del usuario y devuelve la calificación
    num_preguntas = len(preguntas)
    num_respuestas_correctas = 0

    for i, pregunta in enumerate(preguntas):
        respuesta_correcta = pregunta["respuesta_correcta"]
        if respuestas_usuario[i] == respuesta_correcta:
            num_respuestas_correctas += 1

    calificacion = (num_respuestas_correctas / num_preguntas) * 10
    return calificacion

def enviar_email(profesor_email, asunto, mensaje):
    return requests.post(
        f'https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages',
        auth=('api', MAILGUN_API_KEY),
        data={'from': MAILGUN_DOMAIN,
              'to': profesor_email,
              'subject': asunto,
              'text': mensaje})

