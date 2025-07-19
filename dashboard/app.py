import streamlit as st
import requests

st.set_page_config(page_title="Gaia Dashboard", layout="wide")
st.title("Gaia Agent - Live Dashboard")

capsules = requests.get("https://your-render-service.onrender.com/api/capsules").json()
logs = requests.get("https://your-render-service.onrender.com/api/logs").json()

st.subheader("Knowledge Capsules")
st.json(capsules)

st.subheader("Activity Log")
st.json(logs)