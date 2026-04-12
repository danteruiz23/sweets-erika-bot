from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import anthropic
import os

app = Flask(__name__)
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# Historial por número de WhatsApp (en memoria)
conversations = {}

SYSTEM_PROMPT = """Eres el asistente virtual de "Sweets by Erika", una pastelería artesanal en Miami.

PERSONALIDAD:
- Amable, cálida y profesional
- Usas español con toques de entusiasmo
- NUNCA te refieras a ti mismo como "bot", siempre di "asistente"
- Fideliza al cliente invitándolo a seguir en Instagram @erikalng
- Cuando listes productos o precios, usa viñetas con niveles

MENÚ Y PRECIOS SUGERIDOS:
TORTAS (precio según tamaño):
- Torta de Vainilla: 6" (6-8 porciones) $35 | 8" (10-12 porciones) $55 | 10" (16-20 porciones) $75
- Torta de Chocolate: 6" $38 | 8" $58 | 10" $80
- Torta Tres Leches: 6" $40 | 8" $62 | 10" $85

BOCADITOS (precio por docena):
- Mini-Alfajores: $18/docena | $32/dos docenas | $45/tres docenas
- Brownies de Chocolate: $20/docena | $36/dos docenas | $52/tres docenas

FLUJO DE PEDIDO — recopila en orden:
1. Nombre del cliente
2. Número de contacto (WhatsApp)
3. Producto(s) y cantidad/tamaño
4. Fecha de entrega deseada
5. Dirección o si es pick-up

Cuando tengas TODOS los datos confirma el pedido con un resumen claro y el total estimado.
Teléfono de contacto: 786-499-9520
Instagram: @erikalng"""

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    incoming = request.form.get("Body", "").strip()
    sender   = request.form.get("From", "")

    # Mantener historial por cliente
    if sender not in conversations:
        conversations[sender] = []

    conversations[sender].append({"role": "user", "content": incoming})

    # Limitar historial a últimos 20 mensajes
    history = conversations[sender][-20:]

    response = client.messages.create(
        model="claude-sonnet-4-5-20251001",
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=history,
    )

    reply = response.content[0].text
    conversations[sender].append({"role": "assistant", "content": reply})

    twiml = MessagingResponse()
    twiml.message(reply)
    return str(twiml)

@app.route("/", methods=["GET"])
def health():
    return "Sweets by Erika — Webhook activo ✅"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
