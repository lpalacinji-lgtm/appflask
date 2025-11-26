import os
import json
from flask import Flask, render_template, request, redirect, url_for, session, flash
from datetime import datetime
from zoneinfo import ZoneInfo
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd

# -------------------------
# Configuración básica
# -------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "cambia_esta_clave_por_una_muy_larga")

# -------------------------
# Credenciales Google Sheets DESDE VARIABLE DE ENTORNO
# -------------------------
GSHEET_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def get_gspread_client():
    try:
        raw_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")

        if not raw_json:
            raise Exception("La variable de entorno GOOGLE_CREDENTIALS_JSON NO está definida.")

        cred_dict = json.loads(raw_json)

        creds = Credentials.from_service_account_info(
            cred_dict,
            scopes=GSHEET_SCOPES
        )

        client = gspread.authorize(creds)
        return client

    except Exception as e:
        raise Exception(f"Error cargando credenciales: {e}")

# -------------------------
# Helper: cargar hojas y dataframes
# -------------------------
def load_sheets():
    client = get_gspread_client()
    sheet = client.open("FormularioEscaneo")

    base_ws = sheet.worksheet("base_datos")
    reg_ws = sheet.worksheet("registros")

    try:
        df_base = pd.DataFrame(base_ws.get_all_records())
        df_base = df_base.astype(str)
    except:
        df_base = pd.DataFrame(columns=["documento", "nombre completo", "celular"])

    try:
        df_reg = pd.DataFrame(reg_ws.get_all_records())
        df_reg = df_reg.astype(str)
    except:
        df_reg = pd.DataFrame(columns=["timestamp", "documento", "nombre completo", "celular", "datos escaneados"])

    return base_ws, reg_ws, df_base, df_reg

# -------------------------
# Rutas
# -------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    session.setdefault("fase", "formulario")

    try:
        base_ws, reg_ws, df_base, df_reg = load_sheets()
    except Exception as e:
        flash(f"Error accediendo a Google Sheets: {str(e)}", "danger")
        df_base = pd.DataFrame(columns=["documento", "nombre completo", "celular"])
        df_reg = pd.DataFrame()

    if request.method == "POST":
        documento = request.form.get("documento", "").strip()
        if documento == "":
            flash("Ingresa un número de documento.", "warning")
            return redirect(url_for("index"))

        resultado = df_base[df_base["documento"].astype(str) == documento]

        if not resultado.empty:
            fila = resultado.iloc[0]
            session["documento"] = str(documento)
            session["nombre"] = str(fila.get("nombre completo", ""))
            session["celular"] = str(fila.get("celular", ""))

            if not df_reg.empty and documento in df_reg["documento"].astype(str).values:
                fila_reg = df_reg[df_reg["documento"].astype(str) == documento].iloc[0]
                flash("Este documento YA registró un código previamente.", "danger")

                session["last_reg"] = {
                    "codigo": str(fila_reg.get("datos escaneados", "")),
                    "timestamp": str(fila_reg.get("timestamp", "")),
                    "nombre": str(fila_reg.get("nombre completo", "")),
                    "mesa": str(fila_reg.get("zona", "")),
                    "zona": str(fila_reg.get("mesa", ""))
                }
                return redirect(url_for("index"))

            return redirect(url_for("scan"))

        else:
            session["nuevo_documento"] = documento
            return redirect(url_for("nuevo_registro"))

    last_reg = session.pop("last_reg", None)
    return render_template("index.html", last_reg=last_reg)

# -------------------------
# Nuevo registro
# -------------------------
@app.route("/nuevo-registro", methods=["GET", "POST"])
def nuevo_registro():
    nuevo_documento = session.get("nuevo_documento", "")

    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        celular = request.form.get("celular", "").strip()

        if nombre == "" or celular == "":
            flash("Debe ingresar todos los datos.", "warning")
            return redirect(url_for("nuevo_registro"))

        try:
            client = get_gspread_client()
            sheet = client.open("FormularioEscaneo")
            base_ws = sheet.worksheet("base_datos")
            base_ws.append_row([nuevo_documento, nombre, celular])
        except Exception as e:
            flash("Error guardando en base_datos: " + str(e), "danger")
            return redirect(url_for("nuevo_registro"))

        session["documento"] = str(nuevo_documento)
        session["nombre"] = str(nombre)
        session["celular"] = str(celular)
        return redirect(url_for("scan"))

    return render_template("nuevo_registro.html", documento=nuevo_documento)

# -------------------------
# Página de escaneo
# -------------------------
@app.route("/scan", methods=["GET"])
def scan():
    if "documento" not in session:
        flash("Primero realiza la búsqueda de documento.", "warning")
        return redirect(url_for("index"))

    return render_template(
        "scan.html",
        nombre=session.get("nombre", ""),
        documento=session.get("documento", "")
    )

# -------------------------
# Recibir código desde ZXing
# -------------------------
@app.route("/set-codigo", methods=["POST"])
def set_codigo():
    data = request.json or {}
    codigo = data.get("codigo")
    manual = request.form.get("manual_codigo")

    if manual:
        codigo = manual

    if not codigo:
        return {"ok": False, "error": "No llegó el código"}, 400

    session["codigo_detectado"] = str(codigo)
    return {"ok": True}

# -------------------------
# Confirmar y guardar
# -------------------------
@app.route("/confirmar", methods=["GET", "POST"])
def confirmar():

    try:
        base_ws, reg_ws, df_base, df_reg = load_sheets()
    except:
        df_reg = pd.DataFrame()

    codigo = session.get("codigo_detectado", "")

    if request.method == "POST":
        documento = str(session.get("documento"))
        nombre = str(session.get("nombre"))
        celular = str(session.get("celular"))
        codigo_final = str(codigo)

        if not df_reg.empty and documento in df_reg["documento"].astype(str).values:
            flash("Este documento ya tiene registro.", "danger")
            return redirect(url_for("index"))

        if not df_reg.empty and codigo_final in df_reg["datos escaneados"].astype(str).values:
            fila = df_reg[df_reg["datos escaneados"].astype(str) == codigo_final].iloc[0]
            flash(f"Este código ya fue usado por {fila.get('nombre completo')} ({fila.get('documento')})", "danger")
            return redirect(url_for("index"))

        try:
            now = datetime.now(ZoneInfo("America/Bogota")).strftime("%Y-%m-%d %H:%M:%S")
            reg_ws.append_row([now, documento, nombre, celular, codigo_final])
        except Exception as e:
            flash("Error guardando registro: " + str(e), "danger")
            return redirect(url_for("confirmar"))

        flash("Registro guardado correctamente.", "success")
        session.clear()
        return redirect(url_for("index"))

    return render_template(
        "confirmar.html",
        documento=session.get("documento", ""),
        nombre=session.get("nombre", ""),
        celular=session.get("celular", ""),
        codigo=codigo
    )

# -------------------------
# Despliegue
# -------------------------
# Render ejecuta gunicorn automáticamente, NO activar debug aquí.
