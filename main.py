# main.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from paho.mqtt import client as mqtt_client
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import json
import uvicorn
import ssl
import asyncio
from threading import Thread
import paho.mqtt.client as mqtt
import os
from pydantic import BaseModel

# Initialize FastAPI
app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Firebase Init
def initialize_firebase():
    if os.getenv('FIREBASE_CREDENTIALS'):
        # For production: use environment variable
        cred_dict = json.loads(os.getenv('FIREBASE_CREDENTIALS'))
        cred = credentials.Certificate(cred_dict)
    else:
        # For local development: use JSON file
        cred = credentials.Certificate("firebase-credentials.json")
    
    firebase_admin.initialize_app(cred)
    return firestore.client()

db = initialize_firebase()

# Add this after Firebase initialization
try:
    # Test write to Firebase
    test_ref = db.collection('water_levels').add({
        "test": "Connection Test",
        "timestamp": firestore.SERVER_TIMESTAMP
    })
    print("Firebase connection successful!")
except Exception as e:
    print(f"Firebase connection failed: {e}")

# MQTT Config
MQTT_BROKER = "cf6085d108c5492aa2d3ada56f805db4.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USERNAME = "swapisticated"
MQTT_PASSWORD = "awmsMqtt1"
MQTT_TOPIC = "water/level"

def on_connect(client, userdata, flags, rc):
    print(f"Connected with result code {rc}")
    # Subscribe to all relevant topics
    client.subscribe([
        ("water/level", 0),
        ("water/valve", 0)  # Updated to match ESP32's topic
    ])
    print("Subscribed to topics: water/level, water/valve")

def on_message(client, userdata, msg):
    try:
        # Parse the JSON message
        data = json.loads(msg.payload.decode())
        
        # Add timestamp if not present
        if 'timestamp' not in data:
            data['timestamp'] = firestore.SERVER_TIMESTAMP
            
        print(f"Received message on topic: {msg.topic}")
        print(f"Data: {data}")

        # Store in appropriate collection based on topic
        if msg.topic == 'water/level':
            db.collection('water_levels').add(data)
            print(f"Saved water level data")
        elif msg.topic == 'water/valve':  # Updated to match ESP32's topic
            db.collection('valve_status').add(data)
            print(f"Saved valve status data")

    except Exception as e:
        print(f"Error processing message: {e}")
        print(f"Message payload: {msg.payload}")

# Set up MQTT client
client = mqtt.Client()
client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
client.tls_set()

# Assign callbacks
client.on_connect = on_connect
client.on_message = on_message

# Connect to broker
print("Connecting to MQTT broker...")
client.connect(MQTT_BROKER, MQTT_PORT, 60)

# Start the loop
client.loop_forever()

# API Endpoints
@app.get("/water-levels")
async def get_water_levels(limit: int = 10):
    """Get latest water levels"""
    try:
        docs = db.collection('water_levels')\
                 .order_by('timestamp', direction=firestore.Query.DESCENDING)\
                 .limit(limit)\
                 .stream()
        
        return [{"id": doc.id, **doc.to_dict()} for doc in docs]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/water-levels/latest")
async def get_latest_level():
    """Get most recent water level"""
    try:
        docs = db.collection('water_levels')\
                 .order_by('timestamp', direction=firestore.Query.DESCENDING)\
                 .limit(1)\
                 .stream()
        
        for doc in docs:  # Will only run once due to limit(1)
            return {"id": doc.id, **doc.to_dict()}
        
        return {"error": "No data found"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Add new endpoints for valve status
@app.get("/valve-status")
async def get_valve_status(limit: int = 10):
    """Get latest valve status updates"""
    try:
        docs = db.collection('valve_status')\
                 .order_by('timestamp', direction=firestore.Query.DESCENDING)\
                 .limit(limit)\
                 .stream()
        
        return [{"id": doc.id, **doc.to_dict()} for doc in docs]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/valve-status/latest")
async def get_latest_valve_status():
    """Get most recent valve status"""
    try:
        docs = db.collection('valve_status')\
                 .order_by('timestamp', direction=firestore.Query.DESCENDING)\
                 .limit(1)\
                 .stream()
        
        for doc in docs:  # Will only run once due to limit(1)
            return {"id": doc.id, **doc.to_dict()}
        
        return {"error": "No data found"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mqtt.on_message()
async def handle_mqtt_message(client, topic, payload, qos, properties):
    try:
        message = json.loads(payload.decode())
        print(f"Received message on topic {topic}: {message}")

        if topic == "water/readings":
            # Store water level readings as before
            doc_ref = db.collection('water_levels').document()
            message['timestamp'] = firestore.SERVER_TIMESTAMP
            await doc_ref.set(message)

        elif topic == "water/valve_status":
            # Store valve status updates
            doc_ref = db.collection('valve_status').document()
            message['timestamp'] = firestore.SERVER_TIMESTAMP
            await doc_ref.set(message)
            print(f"Stored valve status: {message}")

    except Exception as e:
        print(f"Error processing message: {e}")

class ValveCommand(BaseModel):
    device_id: str
    command: str
    value: int = 0  # Optional with default value 0

@app.post("/valve/command")
async def send_valve_command(command: ValveCommand):
    try:
        # Send command to MQTT as before
        message = {
            "deviceId": command.device_id,
            "command": command.command,
            "value": command.value,
            "timestamp": datetime.now().isoformat()
        }
        await mqtt.publish(f"water/commands", json.dumps(message))

        # Also store initial valve status
        valve_status = {
            "deviceId": command.device_id,
            "isOpen": command.command == "OPEN_VALVE",
            "position": command.value if command.command == "set_valve_position" else None,
            "timestamp": firestore.SERVER_TIMESTAMP
        }
        await db.collection('valve_status').document().set(valve_status)

        return {"status": "success", "message": "Command sent"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)