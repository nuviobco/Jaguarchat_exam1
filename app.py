import uuid
import openai
from flask import Flask, request, jsonify, render_template, send_file, redirect, url_for, flash, session, make_response
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, BooleanField, SelectField
from wtforms.validators import DataRequired, Length, Email, EqualTo
from gtts import gTTS
import os
import re
import tempfile
import nltk
import spacy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import pymongo
import requests
import bcrypt
import json
from datetime import datetime, timedelta
from analisis import obtener_datos, analizar_temas_mas_consultados, contar_palabras, analizar_nivel_comprension, analizar_sentimientos, obtener_horario_mayor_actividad
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import pytz
import examen
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder="static")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "unsecretoaleatorio")
app.config["SESSION_PROTECTION"] = "strong"
scheduler = BackgroundScheduler()
scheduler.start()

api_key = os.getenv("OPENAI_API_KEY")

mongo_uri = os.environ.get("MONGO_URI")
mongo_client = pymongo.MongoClient(mongo_uri)

db = mongo_client["Jaguar"]
col_usuarios = db["usuarios"]
col_historial = db["historial"]

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

class Usuario(UserMixin):
    def __init__(self, user_data):
        self.id = user_data["_id"]
        self.email = user_data["email"]
        self.name = user_data.get("name", "")
        self.first_name = user_data.get("first_name", "")
        self.last_name = user_data.get("last_name", "")
        self.grade = user_data.get("grade", "")
        self.school = user_data.get("school", "")
        self.teacher = user_data.get("teacher", "")


class RegistrationForm(FlaskForm):
    first_name = StringField('Nombre', validators=[DataRequired()])
    last_name = StringField('Apellido', validators=[DataRequired()])
    email = StringField('Correo electrónico', validators=[DataRequired(), Email()])
    password = PasswordField('Contraseña', validators=[DataRequired()])
    confirm_password = PasswordField('Confirmar contraseña', validators=[DataRequired(), EqualTo('password')])
    grade = SelectField('Grado de estudio', choices=[('1', 'Primero'), ('2', 'Segundo'), ('3', 'Tercero'), ('4', 'Cuarto'), ('5', 'Quinto'), ('6', 'Sexto'), ('7', 'Séptimo'), ('8', 'Octavo'), ('9', 'Noveno'), ('10', 'Décimo')], validators=[DataRequired()])
    school = StringField('Colegio', validators=[DataRequired()])
    teacher = StringField('Profesor', validators=[DataRequired()])
    accept_terms = BooleanField('Acepto los términos y condiciones', validators=[DataRequired()])
    submit = SubmitField('Registrarse')

def get_db_connection():
    mongo_uri = os.environ.get('MONGO_URI')
    client = MongoClient(mongo_uri)
    db = client["jaguar_chat"]
    return db

def guardar_historial(user_id, prompt, response):
    fecha = datetime.now()
    conversacion = {
        "user_id": user_id,
        "prompt": prompt,
        "response": response,
        "timestamp": fecha, 
        "tokens_usados": 0 
    }
    col_historial.insert_one(conversacion)

scheduler = BackgroundScheduler()

def contar_tokens(texto):
    return len(texto.split())

def limpiar_historial():
    print("Limpiando historial...")
    fecha_limite = datetime.now(pytz.utc) - timedelta(weeks=1)
    db.historial.delete_many({"timestamp": {"$lt": fecha_limite}})
    print("Historial limpiado.")

trigger = IntervalTrigger(weeks=1)
scheduler.add_job(limpiar_historial, trigger)
scheduler.start()

def enviar_email_mailgun(asunto, contenido, destinatario):
    mailgun_api_key = os.environ.get('MAILGUN_API_KEY')  
    mailgun_domain = os.environ.get('MAILGUN_DOMAIN')  

    url = f"https://api.mailgun.net/v3/{mailgun_domain}/messages"
    auth = ("api", mailgun_api_key)
    data = {
        'from': 'tu_email@example.com',
        'to': destinatario,
        'subject': asunto,
        'text': contenido
    }

    response = requests.post(url, auth=auth, data=data)

    return response

def get_db_connection():
    mongo_uri = os.environ.get("MONGO_URI")
    mongo_client = pymongo.MongoClient(mongo_uri)
    db = mongo_client["jaguar_chat"]
    return db

def obtener_usuario_por_email(email):
    db = get_db_connection()
    usuario = db.usuarios.find_one({"email": email})
    if usuario:
        return usuario
    return None

def guardar_token(_id, token):
    db = get_db_connection()
    db.usuarios.update_one({"_id": _id}, {"$set": {"token": token}})
 
def obtener_id_usuario_por_token(token):
    db = get_db_connection()
    usuario = db.usuarios.find_one({"token": token})
    if usuario:
        return usuario["_id"]
    return None

def actualizar_contraseña(_id, new_password, confirm_password):
    if new_password != confirm_password:
        raise ValueError("Las contraseñas no coinciden")
    
    db = get_db_connection()
    hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
    db.usuarios.update_one({"_id": _id}, {"$set": {"password": hashed_password}})


@app.route('/recuperar_contraseña', methods=['GET','POST'])
def recuperar_contraseña():
    if request.method == 'POST':
        email = request.form['email']
        user = obtener_usuario_por_email(email)
        if user:
            token = generar_token()
            guardar_token(user['_id'], token)
            enviar_email_recuperacion(email, token)
        return render_template('recuperar_contraseña.html', success=True)
    return render_template('recuperar_contraseña.html', success=False)


@app.route('/reset_password/<token>', methods=['GET', 'POST', 'PATCH', 'OPTIONS'])
def reset_password(token):
    if request.method == 'OPTIONS':
        response = make_response()
        response.headers['Allow'] = 'GET, PUT, PATCH, OPTIONS'
        return response

    _id = obtener_id_usuario_por_token(token)
    if not _id:
        return render_template('reset_password.html', error=True)

    if request.method in ['POST', 'PATCH']:
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']
        try:
            actualizar_contraseña(_id, new_password, confirm_password)
        except ValueError as e:
            return render_template('reset_password.html', error=True, message=str(e))
        return render_template('reset_password.html', success=True)

    return render_template('reset_password.html', error=False)

def generar_token():
    return str(uuid.uuid4())

def enviar_email_recuperacion(email, token):
    link = f'https://web-production-7ac0e.up.railway.app/reset_password/{token}'
    subject = 'Recuperación de contraseña'
    html_content = f'<p>Para restablecer tu contraseña, por favor haz clic en el siguiente enlace:</p><p><a href="{link}">{link}</a></p>'
    
    mailgun_api_key = os.environ.get('MAILGUN_API_KEY') 
    mailgun_domain = os.environ.get('MAILGUN_DOMAIN')

    response = requests.post(
        f'https://api.mailgun.net/v3/{mailgun_domain}/messages',
        auth=('api', mailgun_api_key),
        data={
            'from': 'noreply@your_app_domain.com',
            'to': email,
            'subject': subject,
            'html': html_content
        }
    )

    if response.status_code == 200:
        print('Correo electrónico enviado')
    else:
        print('Error al enviar el correo electrónico:', response.status_code)
        print('Detalles del error:', response.text)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    form = RegistrationForm()

    if form.validate_on_submit():
        if form.accept_terms.data:
            email = form.email.data
            password = form.password.data

            user_data = col_usuarios.find_one({"email": email})
            hashed_password = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())

            if not user_data:
                user_data = {
                    "_id": str(uuid.uuid4()),
                    "email": email,
                    "password": hashed_password.decode("utf-8"),
                    "first_name": form.first_name.data,
                    "last_name": form.last_name.data,
                    "grade": form.grade.data,
                    "school": form.school.data,
                    "teacher": form.teacher.data,
                    "tokens_disponibles": 2000,
                }
                col_usuarios.insert_one(user_data)
                flash("Usuario registrado exitosamente. Por favor, inicie sesión.", "success")
                return redirect(url_for("login"))
            else:
                flash("El correo electrónico ya está registrado. Por favor, inicie sesión.", "warning")
        else:
            flash("Debes aceptar los términos y condiciones para registrarte.", "danger")

    return render_template("signup.html", form=form)


@app.route("/terms_and_conditions")
def terms_and_conditions():
    return render_template("terms_and_conditions.html")

@login_manager.user_loader
def load_user(user_id):
    user_data = col_usuarios.find_one({"_id": user_id})
    if user_data:
        return Usuario(user_data)
    return None

def validar_credenciales(email, password):
    user_data = col_usuarios.find_one({"email": email})
    if user_data:
        hashed_password = user_data["password"].encode("utf-8")
        if bcrypt.checkpw(password.encode("utf-8"), hashed_password):
            user = Usuario(user_data)
            login_user(user)
            return True
    return False


nlp = spacy.load('es_core_news_sm')

texto = "Este es un ejemplo de texto para tokenizar."
doc = nlp(texto)

tokens = []
for token in doc:
    tokens.append(token.text)

print(tokens)

nltk.data.path.append("C:/Users/smart/desktop/chatgeo/nltk_data")

texto = "Este es un ejemplo de texto para tokenizar."
tokens = nltk.word_tokenize(texto)

print(tokens)

import openai

load_dotenv()
openai.api_key = api_key


@app.route("/login", methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        
        email = request.form['email']
        password = request.form['password']

        user_id = validar_credenciales(email, password)
        if user_id:
            session['user_id'] = user_id
            return redirect(url_for('home'))
        else:
            flash("Credenciales incorrectas. Por favor, inténtalo de nuevo.")
    return render_template("login.html")

@app.route("/")
def home():
    if current_user.is_authenticated:
        return render_template("index.html")
    else:
        return redirect(url_for("login"))

def es_saludo(texto):

    saludos = ["hola", "saludos", "buenos días", "buenas tardes", "buenas noches", "bienvenidos"]
    texto = texto.lower()
    
    return any(re.search(r'\b' + saludo + r'\b', texto) for saludo in saludos)

def es_tema_educacion_basica(texto):
    palabras_clave_educacion_basica = ["explicar", "que", "como", "donde", "cuando", "quien", "decir en qué consiste", "tiempos verbales", "concepto", "ayudar", "dar ejemplo", "definir", "desarrollar", "qué es", "socializar", "teoremas", "matemáticas", "suma", "resta", "ecuaciones", "grados y tipos", "multiplicación", "división", "matemática general", "aritmética", "álgebra", "geometría", "trigonometría", "estadística", "probabilidad","lengua", "ciencias naturales", "sociales", "presidentes", "historia de Ecuador", "simón bolívar", "ciudades", "capitales", "paises", "historia latinoamericana", "historia universal", "geografía de Ecuador", "cívica", "teorema", "valores", "literatura", "escritura", "gramática", "ortografía", "vocabulario", "fauna", "flora", "álgebra", "aritmética", "cálculo", "fracciones", "geometría", "medidas", "multiplicación", "operaciones combinadas", "problemas de palabras", "proporciones", "regla de tres", "suma", "sustracción", "teorema de pitágoras", "teoría de conjuntos", "teoría de números", "transformaciones geométricas", "ángulos", "decimales", "división", "ecuaciones", "funciones", "geometría analítica", "números enteros", "porcentajes", "potencias", "razones", "sistemas de ecuaciones", "sistemas de numeración", "trigonometría", "unidades de medida", "vectores", "área", "perímetro", "estadística", "probabilidad", "gráficas", "simetría", "transformaciones en el plano", "algoritmos", "patrones numéricos", "geometría espacial", "fracciones equivalentes", "números mixtos", "redondeo de números", "suma de fracciones", "sustracción de fracciones", "multiplicación de fracciones", "división de fracciones", "fracciones impropias", "líneas paralelas", "líneas perpendiculares", "mediana", "moda", "media aritmética", "diagramas de Venn", "números romanos", "cilindro", "cono", "esfera", "poliedros", "polígonos", "propiedades de las operaciones", "fracciones y números mixtos", "porcentajes simples", "porcentajes múltiples", "relaciones de proporcionalidad", "notación científica", "desigualdades", "funciones lineales", "funciones cuadráticas", "ecuaciones de segundo grado", "matrices", "sistemas de matrices", "gráficos circulares", "gráficos de barras", "gráficos de línea", "gráficos de puntos", "estadísticas de dispersión", "regresión lineal", "geometría fractal","comprensión lectora", "ortografía", "redacción", "gramática", "vocabulario", "lectura", "escritura", "literatura infantil", "expresión oral", "interpretación de textos", "tipos de texto", "figuras literarias", "géneros literarios", "análisis literario", "literatura universal", "literatura hispanoamericana", "poesía", "cuento", "novela", "drama", "tragedia", "comedia", "ensayo", "fábula", "leyenda", "mito", "personajes literarios", "técnicas narrativas", "ambiente literario", "contexto literario", "lectura comprensiva", "comprensión auditiva", "estrategias de lectura", "interpretación de poemas", "análisis de textos", "lectura crítica", "literatura clásica", "literatura contemporánea", "literatura fantástica", "literatura de terror", "literatura juvenil", "literatura infantil y juvenil", "literatura gótica", "literatura romántica", "literatura realista", "literatura modernista", "literatura vanguardista", "lenguaje figurado", "uso de la coma", "uso del punto", "uso del punto y coma", "uso de los dos puntos", "uso de las comillas", "uso del paréntesis", "uso del guión", "uso del diéresis", "uso del apóstrofe", "uso del acento", "uso de la tilde", "tipos de palabras", "sinónimos", "antónimos", "homónimos", "polisemia", "paronimia", "afijos", "sufijos", "prefijos", "palabras compuestas", "adjetivos", "adverbios", "verbos", "sustantivos", "pronombres", "artículos", "conjunciones", "preposiciones", "materia", "ciclo vital" "energía", "átomo", "molécula", "elemento", "compuesto", "reacción química", "periodicidad", "fuerzas", "movimiento", "velocidad", "aceleración", "fricción", "leyes de Newton", "gravitación", "termodinámica", "ciclos biogeoquímicos", "ecosistemas", "cadenas alimentarias", "biodiversidad", "evolución", "clasificación de los seres vivos", "adaptación", "mutación", "genes", "herencia", "ADN", "mitosis", "meiosis", "organización celular", "órganos", "sistemas", "respiración", "nutrición", "circulación", "excreción", "homeostasis", "órganos sensoriales", "reflejos", "nervios", "sinapsis", "sistema nervioso", "hormonas", "glándulas", "sistema endocrino", "órganos reproductores", "fecundación", "embarazo", "parto", "desarrollo humano", "enfermedades infecciosas", "vacunas", "antibióticos", "enfermedades crónicas", "cáncer", "contaminación", "efecto invernadero", "cambio climático", "energías renovables", "recursos naturales", "ecología", "delfin", "ballena", "pez", "biotecnología", "nanotecnología", "óptica", "ondas electromagnéticas", "sonido", "electricidad", "magnetismo", "leyes de la electricidad", "circuitos eléctricos", "electrónica", "tecnología", "innovación", "anatomía", "geografía", "historia", "política", "economía", "cultura", "derechos humanos", "democracia", "globalización", "migración", "identidad", "nacionalismo", "multiculturalismo", "racismo", "discriminación", "equidad", "género", "familia", "sociedad", "estado", "ciudadanía", "poder", "participación ciudadana", "organización social", "comunidad", "desigualdad social", "desarrollo sostenible", "recursos naturales", "contaminación", "cambio climático", "biodiversidad", "ecosistemas", "ciencias políticas", "antropología", "sociología", "psicología social", "educación cívica", "patrimonio cultural", "arte", "literatura", "música", "cine", "deporte", "turismo", "religión", "secularismo", "laicidad", "globalismo", "identidades culturales", "interdependencia global", "diversidad cultural", "globalidad", "movimientos sociales", "derecho internacional", "comercio internacional", "crisis migratorias", "conflicto armado", "sistemas políticos", "sistema electoral", "sistema de gobierno", "ciudadanía global", "desarrollo humano", "justicia social", "bienestar social", "relaciones internacionales", "geopolítica", "demografía", "desarrollo económico", "cambio social", "desarrollo social", "derecho internacional humanitario", "terrorismo", "violencia de género", "salud pública", "desastres naturales", "acción humanitaria", "solidaridad", "desarrollo rural", "gobernanza", "política pública", "política exterior", "política social", "política cultural", "política económica", "relaciones de poder", "participación política", "justicia", "etnografía", "criminología", "diversidad funcional", "diversidad sexual", "derechos de autor", "vocabulary building", "grammar rules", "reading comprehension", "listening skills", "pronunciation practice", "conversation practice", "writing practice", "idioms and expressions", "verb tenses", "phrasal verbs", "conditional sentences", "modal verbs", "prepositions usage", "adjectives and adverbs", "nouns and pronouns", "articles usage", "irregular verbs", "comparative and superlative forms", "question formation", "passive voice", "present continuous tense", "past simple tense", "future tense", "conditionals type 1 and 2", "reported speech", "historia y geografía de Ecuador"]
    doc = nlp(texto.lower())
    tokens = [token.lemma_ for token in doc]

    for palabra_clave in palabras_clave_educacion_basica:
        if palabra_clave in tokens:
            return True
    return False

def es_seguimiento(texto):
    seguimientos = ["¿Algo más?", "¿Te ayudo en algo más?", "¿Necesitas algo más?", "¿En qué más te puedo ayudar?"]
    for seguimiento in seguimientos:
        if seguimiento in texto:
            return True
    return False

@app.route('/generate_response', methods=['POST'])
@login_required
def generate_response():
    print(f"Usuario autenticado: {current_user.is_authenticated}")

    if not current_user.is_authenticated:
        return jsonify({"error": "Usuario no autenticado"}), 401

    prompt = request.json["prompt"]

    usuario = col_usuarios.find_one({"_id": current_user.id})
    if 'tokens_usados' not in usuario:
        col_usuarios.update_one({"_id": current_user.id}, {"$set": {"tokens_usados": 0}})
        usuario = col_usuarios.find_one({"_id": current_user.id})

    limite_tokens = 2000

    if usuario.get('tokens_usados', 0) >= limite_tokens:
        return jsonify({"error": "Límite de tokens alcanzado", "tokens_usados": usuario['tokens_usados']}), 402
    

    response = openai.Completion.create(
        engine="text-davinci-003",
        prompt=f"hola, buenos días, buenas tardes, buenas noches, saludos, qué, cómo, donde, cuándo, calcula, cuanto, por favor, cuantos grados, cuantos tipos, por qué, quien, de qué forma, de qué manera, dame, ejercicios, concepto, definición, cuál, cuales, figurar, desarrolar, cuando nació, ser muy amigable en el contexto de la educación básica en: 1. matemáticas (resolver, suma, resta, multiplicación, división, álgebra, geometría, fracciones, decimales, porcentajes, resolución de problemas, estadística, cómo se calcula, como se escribe, cúal es la fórmula, que ejercicos, resolver, etc.), 2. lengua y literatura (gramática, ortografía, tiempos verbales, vocabulario, lectura, escritura creativa, análisis de textos literarios, poesía, etc.), 3. ciencias naturales (biología, física, química, medio ambiente, cambio climático, energía, tecnología, salud, etc.), 4. estudios sociales (historia, geografía, ciudades, capitales, paises, continentes, simón bolívar, colonia, independencia, eloy alfaro, provincias, rios, montañas, volcanes, islas, américa latina, civismo, cultura, derechos humanos, democracia, economía, etc.), 5. habilidades comunicativas en inglés (vocabulario, gramática, conversación, lectura, escritura, pronunciación, etc.). saludar, agradecer, felicitar, agradecer. responde: {prompt}",
        max_tokens=200,
        n=1,
        stop=None,
        temperature=0.5,
    ).choices[0].text.strip()

    col_historial.insert_one({
        "user_id": current_user.id,
        "prompt": prompt,
        "response": response,
        "timestamp": datetime.now(pytz.utc)
    })

    intentos = 0
    while not es_tema_educacion_basica(response) and intentos < 1:
        response = openai.Completion.create(
            engine="text-davinci-003",
            prompt=f"hola, buenos días, buenas tardes, buenas noches, saludos, qué, cómo, donde, cuándo, calcula, cuanto, por favor, cuantos grados, cuantos tipos, por qué, quien, de qué forma, de qué manera, dame, ejercicios, concepto, definición, cuál, cuales, figurar, desarrolar, cuando nació, ser muy amigable en el contexto de la educación básica en: 1. matemáticas (resolver, suma, resta, multiplicación, división, álgebra, geometría, fracciones, decimales, porcentajes, resolución de problemas, estadística, cómo se calcula, como se escribe, cúal es la fórmula, que ejercicos, resolver, etc.), 2. lengua y literatura (gramática, ortografía, tiempos verbales, vocabulario, lectura, escritura creativa, análisis de textos literarios, poesía, etc.), 3. ciencias naturales (biología, física, química, medio ambiente, cambio climático, energía, tecnología, salud, etc.), 4. estudios sociales (historia, geografía, ciudades, capitales, paises, continentes, simón bolívar, colonia, independencia, eloy alfaro, provincias, rios, montañas, volcanes, islas, américa latina, civismo, cultura, derechos humanos, democracia, economía, etc.), 5. habilidades comunicativas en inglés (vocabulario, gramática, conversación, lectura, escritura, pronunciación, etc.). saludar, agradecer, felicitar, agradecer. responde: {prompt}",
            n=1,
            stop=None,
            temperature=0.5,
        ).choices[0].text.strip()
    intentos += 1

    tokens_usados = contar_tokens(prompt) + contar_tokens(response)
    col_usuarios.update_one({"_id": current_user.id}, {"$inc": {"tokens_usados": tokens_usados}})

    if usuario.get('tokens_usados', 0) >= limite_tokens:
        return jsonify({"error": "Límite de tokens alcanzado", "tokens_usados": usuario['tokens_usados']}), 402
    
    if es_saludo(response):
        return jsonify({"response": "¡Hola! Soy jaguar chat, un bot educativo. ¿En qué puedo ayudarte?", "tokens_usados": usuario['tokens_usados'] + tokens_usados})
    elif "gracias" in response.lower():
        return jsonify({"response": "¡De nada! Estoy aquí para ayudarte en lo que necesites.", "tokens_usados": usuario['tokens_usados'] + tokens_usados})
    elif es_seguimiento(response):
        return jsonify({"response": "Claro, ¿qué otra duda tienes?", "tokens_usados": usuario['tokens_usados'] + tokens_usados})
    elif es_tema_educacion_basica(response):
        return jsonify({"response": response, "tokens_usados": usuario['tokens_usados'] + tokens_usados})
    else:
        return jsonify({"response": "Lo siento, no entendí tu pregunta. ¿Podrías reformularla con respecto a la educación básica?", "tokens_usados": usuario['tokens_usados'] + tokens_usados})

@app.route("/speak/<text>")
def speak(text):
    tts = gTTS(text=text, lang="es")
    with tempfile.NamedTemporaryFile(delete=True) as fp:
        tts.save(fp.name)
        return send_file(fp.name, mimetype="audio/mpeg")


@app.route('/')
def index():
    return render_template('index.html')

from json import JSONDecodeError

@app.route('/update_session', methods=['POST'])
def update_session():
    try:
        data = json.loads(request.data)
    except JSONDecodeError:
        return 'Error al procesar datos JSON', 400

    pregunta = data['pregunta']
    respuesta = data['respuesta']

    if 'historial' not in session:
        session['historial'] = []

    session['historial'].append({
        
    'fecha': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    'pregunta': pregunta,
    'respuesta': respuesta
})

    return '', 200

@app.route('/historial')
@login_required
def historial():
    user_id = str(current_user.get_id())
    historial_usuario = list(col_historial.find({"user_id": user_id}))

    historial_usuario = historial_usuario[-15:]

    ecuador_tz = pytz.timezone('America/Guayaquil') 

    historial = [
        {
            "fecha": item["timestamp"].astimezone(ecuador_tz).strftime('%Y-%m-%d %H:%M:%S') if "timestamp" in item else "N/A",
            "pregunta": item["prompt"],
            "respuesta": item["response"],
        }
        for item in historial_usuario if item["response"] != "Lo siento, no entendí tu pregunta. ¿Podrías reformularla con respecto a la educación básica?"
    ]
    nombre_completo = f"{current_user.first_name} {current_user.last_name}"
    colegio = current_user.school
    grado = current_user.grade
    profesor = current_user.teacher

    print("Objeto historial:", historial)

    return render_template('historial.html', historial=historial, user_id=user_id, nombre=nombre_completo, colegio=colegio, grado=grado, profesor=profesor)
    
@app.route('/ver_tokens')
@login_required
def ver_tokens():
    usuario = col_usuarios.find_one({"_id": current_user.id})
    tokens_usados = usuario.get('tokens_usados', 0)

    print("Tokens usados:", tokens_usados)

    return jsonify({"tokens_usados": tokens_usados})

@app.route('/pagina_pago', methods=['GET'])
def pagina_pago():
    return render_template('pagina_pago.html')

@app.route('/analisis/<user_id>')
@login_required
def analisis(user_id):
    prompts = obtener_datos(user_id)

    temas_consultados = analizar_temas_mas_consultados(prompts)
    print("Temas consultados:", temas_consultados)

    palabras_contadas = list(contar_palabras(prompts).items())
    print("Palabras contadas:", palabras_contadas)

    horas_mayor_actividad = obtener_horario_mayor_actividad(prompts)
    print("Horas mayor actividad:", horas_mayor_actividad)

    nivel_comprension = analizar_nivel_comprension(prompts)
    print("Nivel comprensión:", nivel_comprension)

    sentimientos = analizar_sentimientos(prompts)
    print("Sentimientos:", sentimientos)

    nombre_completo = f"{current_user.first_name} {current_user.last_name}"
    colegio = current_user.school
    grado = current_user.grade
    profesor = current_user.teacher

    return render_template('analisis.html',
                           temas_consultados=temas_consultados,
                           palabras_contadas=palabras_contadas,
                           horas_mayor_actividad=horas_mayor_actividad,
                           nivel_comprension=nivel_comprension,
                           sentimientos=sentimientos,
                           user_id=user_id,
                           nombre_completo=nombre_completo, 
                           colegio=colegio,
                           grado=grado,
                           profesor=profesor)


def obtener_credenciales_email(user_id):
    mongo_uri = os.environ.get("MONGO_URI")
    mongo_client = pymongo.MongoClient(mongo_uri)
    db = mongo_client["jaguar_chat"]

    col_usuarios = db["usuarios"]

    usuario = col_usuarios.find_one({'user_id': user_id})

    if usuario:
        
        return usuario['email'], usuario['password']
    else:
        return None, None
    ...

def obtener_datos_usuario(user_id):
    mongo_uri = os.environ.get("MONGO_URI")
    mongo_client = pymongo.MongoClient(mongo_uri)
    db = mongo_client["jaguar_chat"]

    col_usuarios = db["usuarios"]

    usuario = col_usuarios.find_one({'user_id': user_id})

    if usuario:
        return {
            'nombre': usuario['first_name'] + ' ' + usuario['last_name'],
            'colegio': usuario['colegio'],
            'grado': usuario['grado'],
            'profesor': usuario['profesor']
        }
    else:
        return {}

@app.route('/enviar_analisis', methods=['GET', 'POST'])
def enviar_analisis():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))
    if request.method == 'POST':
        profesor_email = request.form['correo_profesor']
    
        resultados_analisis = analisis(user_id)

        email_usuario, _ = obtener_credenciales_email(user_id)

        asunto = "Resultados del análisis"

        contenido = str(resultados_analisis)

        try:
            response = enviar_email_mailgun(asunto, contenido, profesor_email)
            if response.status_code == 200:
                return render_template('resultado_envio.html', enviado=True)
            else:
                print("Error al enviar el correo electrónico:", response.status_code)
                return render_template('resultado_envio.html', enviado=False)
        except Exception as e:
            print("Error al enviar el correo electrónico:", e)
            return render_template('resultado_envio.html', enviado=False)

    return render_template('enviar_analisis.html')


@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route("/<path:path>")
def catch_all(path):
    return render_template("404.html"), 404

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
    app.run(debug=True)