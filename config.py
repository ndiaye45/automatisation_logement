# ══════════════════════════════════════════════════
#  config.py  –  Surveillance CROUS Orléans
# ══════════════════════════════════════════════════

import os

EMAIL_CONFIG = {
    "smtp_host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
    "smtp_port": int(os.environ.get("SMTP_PORT", 465)),

    # Compte Gmail qui ENVOIE (utilise un mot de passe d'application, pas ton vrai mdp)
    # Tuto : myaccount.google.com → Sécurité → Mots de passe des applications
    "sender":    os.environ.get("SENDER", "yousndiaye926@gmail.com"),      # ← remplace
    "password":  os.environ.get("PASSWORD", "oipw dggs bzao cnud"),      # ← 16 caractères générés par Google

    # Adresse qui REÇOIT les alertes (peut être la même)
    "recipient": os.environ.get("RECIPIENT", "yousndiaye926@gmail.com"),      # ← remplace
}

# Fréquence de vérification (en minutes)
# 15 min = réactif | 30 min = raisonnable | 60 min = discret
INTERVALLE_MINUTES = int(os.environ.get("INTERVALLE_MINUTES", 10))