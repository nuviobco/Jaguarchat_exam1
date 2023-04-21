import pandas as pd
import spacy
import os
import spacy
import pytz 
from datetime import datetime
from textstat import textstat
import pymongo 
from pymongo import MongoClient
from textblob import TextBlob
from collections import defaultdict

nlp = spacy.load('es_core_news_sm')


def obtener_datos(user_id):

    mongo_uri = os.environ.get("MONGO_URI")
    mongo_client = pymongo.MongoClient(mongo_uri)
    db = mongo_client["jaguar_chat"]

    col_historial = db["historial"]


    cursor = col_historial.find({'user_id': user_id})


    prompts = [doc for doc in cursor]
    print("Datos obtenidos:", prompts)  
    return prompts

def contar_palabras(prompts, campo='prompt', num_palabras=10):
    nlp = spacy.load("es_core_news_sm")
    conteo_palabras = defaultdict(int)

    for prompt in prompts:
        texto = prompt.get(campo)
        if texto is not None:
            doc = nlp(texto)
            for token in doc:
                if token.is_alpha and not token.is_stop and token.pos_ in ['NOUN', 'VERB', 'ADJ', 'ADV']:
                    conteo_palabras[token.text.lower()] += 1
    conteo_palabras = dict(sorted(conteo_palabras.items(), key=lambda x: x[1], reverse=True)[:num_palabras])

    return dict(conteo_palabras)


def obtener_horario_mayor_actividad(prompts):
    conteo_horas = [0] * 24
    zona_horaria = pytz.timezone('America/Guayaquil')  

    for prompt in prompts:
        try:
            fecha = prompt['timestamp'] if 'timestamp' in prompt else prompt['tamp']
            if isinstance(fecha, str):
                fecha_hora = datetime.strptime(fecha, "%Y-%m-%d %H:%M:%S.%f")
            else:
                fecha_hora = fecha

            fecha_hora_local = fecha_hora.astimezone(zona_horaria)  
            hora = fecha_hora_local.hour
            conteo_horas[hora] += 1
        except KeyError:
            print(f"No se encontró 'timestamp' ni 'tamp' en el prompt: {prompt}")

    horas_mayor_actividad = [(i, conteo) for i, conteo in enumerate(conteo_horas)]
    horas_mayor_actividad = sorted(horas_mayor_actividad, key=lambda x: x[1], reverse=True)[:10]

    return horas_mayor_actividad


def analizar_nivel_comprension(prompts, limit=10):
    resultados = []

    for prompt in prompts:
        texto = prompt.get('prompt')
        if texto is not None:
            doc = nlp(texto)
            nivel_comprension = textstat.flesch_kincaid_grade(texto)
            resultados.append({'texto': texto, 'nivel_comprension': nivel_comprension})
    resultados = sorted(resultados, key=lambda x: x['nivel_comprension'], reverse=True)[:limit]

    return resultados


def analizar_sentimientos(prompts, limit=10):
    resultados = []

    malas_palabras = ['chucha', 'malo', 'marico', 'chingar', 'wey', 'pendejo', 'cabrón', 'puta', ]
    palabras_agradecimiento = ['gracias', 'agradecido', 'agradecimiento', 'super', 'maravilloso', 'exitante', 'cariñoso', 'respetuoso']

    for prompt in prompts:
        texto = prompt.get('prompt')
        if texto is not None:
            doc = nlp(texto)

            puntaje_sentimiento = TextBlob(texto).sentiment.polarity

            for palabra in malas_palabras:
                if palabra.lower() in texto.lower():
                    puntaje_sentimiento -= 0.5

            for palabra in palabras_agradecimiento:
                if palabra.lower() in texto.lower():
                    puntaje_sentimiento += 0.5

            if puntaje_sentimiento > 0:
                etiqueta_sentimiento = "Positivo"
            elif puntaje_sentimiento < 0:
                etiqueta_sentimiento = "Negativo"
            else:
                etiqueta_sentimiento = "Neutral"

            resultados.append({'texto': texto, 'puntaje_sentimiento': puntaje_sentimiento, 'etiqueta_sentimiento': etiqueta_sentimiento})
    resultados = sorted(resultados, key=lambda x: abs(x['puntaje_sentimiento']), reverse=True)[:limit]

    return resultados


def analizar_temas_mas_consultados(prompts, num_temas=10):
    nlp = spacy.load("es_core_news_sm")
    conteo_temas = defaultdict(int)

    for prompt in prompts:
        texto = prompt.get('prompt')
        if texto is not None:
            doc = nlp(texto)
            palabras_relevantes = [token.text.lower() for token in doc if token.is_alpha and not token.is_stop and token.pos_ in ['NOUN', 'VERB', 'ADJ', 'ADV']]
            for palabra in palabras_relevantes:
                conteo_temas[palabra] += 1

    temas_ordenados = sorted(conteo_temas.items(), key=lambda x: x[1], reverse=True)
    temas_ordenados = temas_ordenados[:num_temas]
    return temas_ordenados


