from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
import anthropic
import stripe
import os
import json
import re

app = Flask(__name__)

anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
twilio_client = TwilioClient(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])

ERIKA_WHATSAPP = os.environ["ERIKA_WHATSAPP"]   # ej: whatsapp:+13055551234
TWILIO_FROM     = os.environ["TWILIO_FROM"]      # ej: whatsapp:+14155238886

conversations = {}

SYSTEM_PROMPT = """Eres el asistente virtual de "Sweets by Erika", una pastelería artesanal en Miami.

PERSONALIDAD:
- Amable, cálida y profesional
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

Cuando tengas TODOS los datos, confirma el resumen y el total, luego incluye al final:
###ORDER_COMPLETE###
{"nombre":"...","productos":"...","fecha":"...","entrega":"...","total":0}
###END_ORDER###

Teléfono: 786-499-9520 | Instagram: @erikalng"""


def parse_order(text):
    m = re.search(r'###ORDER_COMPLETE###\s*(.*?)\s*###END_ORDER###', text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except:
        return None


def clean_text(text):
    return re.sub(r'###ORDER_COMPLETE###.*?###END_ORDER###', '', text, flags=re.DOTALL).strip()


def create_stripe_link(order):
    total_cents = int(order.get("total", 0) * 100)
    if total_cents <= 0:
        total_cents = 5000  # fallback $50

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "usd",
                "product_data": {
                    "name": f"Sweets by Erika — Pedido de {order.get('nombre','Cliente')}",
                    "description": order.get("productos", "Pedido artesanal"),
                },
                "unit_amount": total_cents,
            },
            "quantity": 1,
        }],
        mode="payment",
        success_url="https://instagram.com/erikalng",
        cancel_url="https://instagram.com/erikalng",
        metadata={
            "nombre": order.get("nombre", ""),
            "fecha": order.get("fecha", ""),
            "entrega": order.get("entrega", ""),
        }
    )
    return session.url


def notify_erika(order, payment_url):
    msg = (
        f"🎂 *Nuevo pedido recibido!*\n\n"
        f"👤 Cliente: {order.get('nombre')}\n"
        f"🛍️ Pedido: {order.get('productos')}\n"
        f"📅 Fecha: {order.get('fecha')}\n"
        f"📍 Entrega: {order.get('entrega')}\n"
        f"💰 Total: ${order.get('total')}\n\n"
        f"🔗 Link de pago:\n{payment_url}"
    )
    twilio_client.messages.create(
        body=msg,
        from_=TWILIO_FROM,
        to=ERIKA_WHATSAPP
    )


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    incoming = request.form.get("Body", "").strip()
    sender   = request.form.get("From", "")

    if sender not in conversations:
        conversations[sender] = []

    conversations[sender].append({"role": "user", "content": incoming})
    history = conversations[sender][-20:]

    response = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        system=SYSTEM_PROMPT,
        messages=history,
    )

    raw   = response.content[0].text
    order = parse_order(raw)
    clean = clean_text(raw)

    conversations[sender].append({"role": "assistant", "content": clean})

    reply = clean

    if order:
        try:
            payment_url = create_stripe_link(order)
            reply += f"\n\n💳 *Link de pago:*\n{payment_url}"
            notify_erika(order, payment_url)
        except Exception as e:
            print(f"Stripe/Twilio error: {e}")

    twiml = MessagingResponse()
    twiml.message(reply)
    return str(twiml)


@app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig     = request.headers.get("Stripe-Signature", "")
    secret  = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig, secret)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    if event["type"] == "checkout.session.completed":
        session  = event["data"]["object"]
        nombre   = session["metadata"].get("nombre", "Cliente")
        total    = session["amount_total"] / 100
        twilio_client.messages.create(
            body=f"✅ *¡Pago confirmado!*\n👤 {nombre}\n💰 ${total:.2f}\n\n¡Manos a la obra! 🎂",
            from_=TWILIO_FROM,
            to=ERIKA_WHATSAPP
        )

    return jsonify({"status": "ok"})


@app.route("/", methods=["GET"])
def health():
    return "Sweets by Erika — Webhook activo ✅"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

