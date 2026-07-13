import os
import streamlit as pd
import streamlit as st
import requests
import pandas as pd
from sqlalchemy import create_engine

# Configuration de la page
st.set_page_config(
    page_title="Dedall Energy - Outreach Dashboard",
    page_icon="⚡",
    layout="wide"
)

# Configuration API et Base de données
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/postgres")
# On cible l'URL locale ou Railway de ton FastAPI (par défaut port 8080 en interne)
FASTAPI_URL = os.getenv("FASTAPI_URL", "http://localhost:8080")

# Connexion BDD pour récupérer les leads en temps réel
@st.cache_data(ttl=5) # Rafraîchissement toutes les 5 secondes
def get_leads_from_db():
    try:
        engine = create_engine(DATABASE_URL)
        query = "SELECT id, company_name, energy_intensity, priority_score, status, email_subject, email_body, video_url, raw_data FROM leads ORDER BY id DESC"
        df = pd.read_sql(query, engine)
        return df
    except Exception as e:
        st.error(f"Erreur de connexion à la base de données : {e}")
        return pd.DataFrame()

# --- HEADER ---
st.title("⚡ Dedall Energy — Outreach Engine")
st.subheader("Dashboard de suivi et génération de leads automatisée")
st.markdown("---")

# --- PANNEL DE GAUCHE : SOURCING & TRIGGER ---
with st.sidebar:
    st.header("🔍 Sourcing de prospects")
    query_input = st.text_input("Saisir une recherche (ex: Boulangerie Paris)", placeholder="Boulangerie Paris")
    
    if st.button("🚀 Lancer le Pipeline", use_container_width=True):
        if query_input:
            with st.spinner("Lancement du pipeline en tâche de fond..."):
                try:
                    res = requests.post(f"{FASTAPI_URL}/trigger-pipeline", json={"query": query_input})
                    if res.status_code == 200:
                        st.success(f"Pipeline activé pour '{query_input}' !")
                        st.info("Les résultats vont apparaître dans le tableau d'ici quelques instants.")
                    else:
                        st.error(f"Erreur API ({res.status_code}) : {res.text}")
                except Exception as e:
                    st.error(f"Impossible de joindre l'API FastAPI : {e}")
        else:
            st.warning("Veuillez saisir une recherche avant de lancer.")

    st.markdown("---")
    st.header("📥 Actions de masse")
    min_score = st.slider("Score minimum pour envoi Outreach", 1, 10, 7)
    if st.button("📨 Envoyer les leads qualifiés", use_container_width=True):
        with st.spinner("Envoi des webhooks en cours..."):
            try:
                res = requests.post(f"{FASTAPI_URL}/send-pending-leads?min_score={min_score}")
                if res.status_code == 200:
                    data = res.json()
                    st.success(f"Terminé ! {data.get('sent_count', 0)} leads envoyés.")
                    st.rerun()
                else:
                    st.error(f"Erreur lors de l'envoi : {res.text}")
            except Exception as e:
                st.error(f"Erreur : {e}")

# --- ZONE PRINCIPALE : TABLEAU ET REVUE ---
df_leads = get_leads_from_db()

if df_leads.empty:
    st.info("Aucun prospect trouvé dans la base de données. Utilisez le panneau de gauche pour lancer une recherche.")
else:
    # Métriques rapides
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Prospects", len(df_leads))
    col2.metric("En attente (QUALIFIED)", len(df_leads[df_leads['status'] == 'QUALIFIED']))
    col3.metric("Envoyés (SENT)", len(df_leads[df_leads['status'] == 'SENT']))

    st.markdown("### 📋 Liste des prospects identifiés")
    
    # Configuration des colonnes pour un affichage propre
    st.dataframe(
        df_leads[['id', 'company_name', 'energy_intensity', 'priority_score', 'status']],
        column_config={
            "id": "ID",
            "company_name": "Entreprise",
            "energy_intensity": "Intensité Énergétique",
            "priority_score": st.column_config.ProgressColumn("Score de Priorité", min_value=0, max_value=10, format="%d"),
            "status": "Statut"
        },
        use_container_width=True,
        hide_index=True
    )

    st.markdown("---")
    st.markdown("### 🔍 Inspection et Revue d'un Prospect")
    
    # Sélecteur de lead pour voir le détail
    selected_company = st.selectbox("Sélectionnez une entreprise pour analyser son dossier :", df_leads['company_name'].unique())
    
    if selected_company:
        lead_detail = df_leads[df_leads['company_name'] == selected_company].iloc[0]
        
        c_left, c_right = st.columns([1, 1])
        
        with c_left:
            st.markdown(f"#### ✉️ Email rédigé par l'IA")
            st.text_input("Objet du mail :", value=lead_detail['email_subject'], disabled=True)
            st.text_area("Corps du mail :", value=lead_detail['email_body'], height=250, disabled=True)
            st.caption(f"**Données brutes détectées :** {lead_detail['raw_data']}")
            
        with c_right:
            st.markdown("#### 🎬 Vidéo Personnalisée HeyGen")
            if lead_detail['video_url']:
                st.video(lead_detail['video_url'])
                st.caption(f"Lien direct de la vidéo : [Ouvrir]({lead_detail['video_url']})")
            else:
                if lead_detail['priority_score'] >= 7:
                    st.warning("⚠️ La vidéo a échoué au rendu (Vérifier le solde de crédits HeyGen).")
                else:
                    st.info("ℹ️ Aucune vidéo générée (Score de priorité inférieur à 7).")
