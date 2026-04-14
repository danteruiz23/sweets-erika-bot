from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
import anthropic
import stripe
from supabase import create_client
import os
import json
import re

app = Flask(__name__)

anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
twilio_client = TwilioClient(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

ERIKA_WHATSAPP = os.environ["ERIKA_WHATSAPP"]
TWILIO_FROM    = os.environ["TWILIO_FROM"]

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

Cuando tengas TODOS los datos, confirma el resumen y el total. Luego pregunta:
"¿Cómo prefieres pagar? 💳 Tarjeta o 🏦 Zelle"

Si el cliente elige TARJETA, incluye al final:
###ORDER_COMPLETE###
{"nombre":"...","contacto":"...","productos":"...","fecha":"...","entrega":"...","total":0,"pago":"tarjeta"}
###END_ORDER###

Si el cliente elige ZELLE, incluye al final el mensaje de instrucciones y:
###ORDER_ZELLE###
{"nombre":"...","contacto":"...","productos":"...","fecha":"...","entrega":"...","total":0,"pago":"zelle"}
###END_ORDER###

Para pagos con Zelle di exactamente:
"Para completar tu pedido, envía $[TOTAL] por Zelle a:
📱 786-499-9520
👤 Nombre: Erika L.
Una vez realizado el pago, envíanos una captura de pantalla y confirmamos tu pedido de inmediato. ¡Gracias!"

Teléfono: 786-499-9520 | Instagram: @erikalng"""


def parse_order(text):
    for tag in ["ORDER_COMPLETE", "ORDER_ZELLE"]:
        m = re.search(rf'###{tag}###\s*(.*?)\s*###END_ORDER###', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except:
                return None
    return None

def clean_text(text):
    return re.sub(r'###ORDER_(COMPLETE|ZELLE)###.*?###END_ORDER###', '', text, flags=re.DOTALL).strip()


def create_stripe_link(order):
    total_cents = int(float(order.get("total", 0)) * 100)
    if total_cents <= 0:
        total_cents = 5000

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
    return session


def save_order(order, payment_url, session_id):
    try:
        supabase.table("pedidos").insert({
            "nombre": order.get("nombre", ""),
            "contacto": order.get("contacto", ""),
            "productos": order.get("productos", ""),
            "fecha_entrega": order.get("fecha", ""),
            "entrega": order.get("entrega", ""),
            "total": order.get("total", 0),
            "estado": "pendiente",
            "stripe_url": payment_url,
            "stripe_session_id": session_id,
        }).execute()
    except Exception as e:
        print(f"Supabase error: {e}")


def update_order_status(session_id, status):
    try:
        supabase.table("pedidos").update({"estado": status}).eq("stripe_session_id", session_id).execute()
    except Exception as e:
        print(f"Supabase update error: {e}")


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
        pago = order.get("pago", "tarjeta")
        try:
            if pago == "tarjeta":
                session     = create_stripe_link(order)
                payment_url = session.url
                reply      += f"\n\n💳 *Link de pago:*\n{payment_url}"
                save_order(order, payment_url, session.id)
                notify_erika(order, payment_url)
            else:
                save_order(order, "zelle", "zelle-" + order.get("nombre","").replace(" ","-"))
                notify_erika(order, f"Pago por Zelle — ${order.get('total')} pendiente de confirmación")
        except Exception as e:
            print(f"Error procesando orden: {e}")

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
        session    = event["data"]["object"]
        nombre     = session["metadata"].get("nombre", "Cliente")
        total      = session["amount_total"] / 100
        session_id = session["id"]
        update_order_status(session_id, "pagado")
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
