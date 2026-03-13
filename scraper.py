"""
🎓 Surveillant CROUS – Résidences universitaires Orléans
API : https://trouverunlogement.lescrous.fr/tools/42/search
Envoie un email dès qu'un nouveau logement est disponible.
"""

import requests
import json
import os
import smtplib
import hashlib
import time
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from config import EMAIL_CONFIG, INTERVALLE_MINUTES

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("crous_watch.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────
# API JSON interne du CROUS (reverse-engineered depuis le site)
# Les bounds correspondent exactement à ton lien Orléans
API_URL = "https://trouverunlogement.lescrous.fr/api/v1/tools/42/search"
PARAMS  = {
    "bounds": "1.8757578_47.9335389_1.9487114_47.8132802",
    "page":   1,
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json",
    "Referer":    "https://trouverunlogement.lescrous.fr/",
}

STATE_FILE = "crous_seen.json"
BASE_URL   = "https://trouverunlogement.lescrous.fr/tools/42/accommodations/"


# ── State ─────────────────────────────────────────────────────────────────────
def load_seen() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    with open(STATE_FILE, "w") as f:
        json.dump(list(seen), f)


# ── Appel API CROUS ───────────────────────────────────────────────────────────
def fetch_logements() -> list[dict]:
    """
    Récupère tous les logements disponibles via l'API JSON du CROUS.
    Gère la pagination automatiquement.
    """
    all_logements = []
    page = 1

    while True:
        try:
            p = dict(PARAMS)
            p["page"] = page
            r = requests.get(API_URL, params=p, headers=HEADERS, timeout=15)

            if r.status_code != 200:
                log.warning(f"API CROUS status {r.status_code} – page {page}")
                # Fallback : scraping HTML de la page de recherche
                return fetch_logements_html()

            data = r.json()

            # Structure typique : { "data": [...], "meta": { "last_page": N } }
            items = (
                data.get("data") or
                data.get("_embedded", {}).get("accommodations", []) or
                data.get("items") or
                (data if isinstance(data, list) else [])
            )

            if not items:
                break

            for item in items:
                logement = parse_logement(item)
                if logement:
                    all_logements.append(logement)

            # Pagination
            last_page = (
                data.get("meta", {}).get("last_page") or
                data.get("last_page") or
                1
            )
            if page >= last_page:
                break
            page += 1

        except (ValueError, KeyError):
            # Réponse non-JSON → fallback HTML
            log.info("Réponse non-JSON, passage au scraping HTML...")
            return fetch_logements_html()
        except requests.RequestException as e:
            log.error(f"Erreur réseau : {e}")
            break

    log.info(f"CROUS API : {len(all_logements)} logement(s) trouvé(s)")
    return all_logements


def parse_logement(item: dict) -> dict | None:
    """Extrait les infos utiles d'un objet logement CROUS."""
    try:
        # Les champs varient légèrement selon la version de l'API
        logement_id = str(
            item.get("id") or
            item.get("roomId") or
            item.get("accommodationId") or
            hashlib.md5(str(item).encode()).hexdigest()
        )

        title = (
            item.get("title") or
            item.get("label") or
            item.get("name") or
            item.get("roomType") or
            "Logement CROUS"
        )

        residence = (
            item.get("residence", {}).get("label") or
            item.get("building", {}).get("label") or
            item.get("residenceName") or
            ""
        )

        price = item.get("price") or item.get("rent") or item.get("monthlyRent") or ""
        if price:
            price = f"{price} €/mois"

        url = f"{BASE_URL}{logement_id}"

        address = (
            item.get("address") or
            item.get("residence", {}).get("address") or
            ""
        )

        label = f"{title}"
        if residence:
            label += f" – {residence}"
        if price:
            label += f" ({price})"

        return {
            "id":      logement_id,
            "title":   label,
            "url":     url,
            "price":   price,
            "address": address,
        }
    except Exception:
        return None


def fetch_logements_html() -> list[dict]:
    """
    Fallback : scraping HTML de la page de résultats CROUS.
    Utilisé si l'API JSON ne répond pas comme attendu.
    """
    from bs4 import BeautifulSoup
    listings = []
    url = (
        "https://trouverunlogement.lescrous.fr/tools/42/search"
        "?bounds=1.8757578_47.9335389_1.9487114_47.8132802"
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        # Sélecteurs potentiels pour les cartes de logement
        cards = (
            soup.select("li.fr-card") or
            soup.select("article.accommodation") or
            soup.select(".fr-card") or
            soup.select("li[class*='accommodation']") or
            soup.select("a[href*='/accommodations/']")
        )

        for card in cards:
            text  = card.get_text(separator=" ", strip=True)[:150]
            link  = card.find("a", href=True)
            href  = link["href"] if link else ""
            if not href.startswith("http"):
                href = "https://trouverunlogement.lescrous.fr" + href
            lid = hashlib.md5(text.encode()).hexdigest()
            listings.append({"id": lid, "title": text, "url": href, "price": "", "address": ""})

        log.info(f"CROUS HTML fallback : {len(listings)} logement(s)")
    except Exception as e:
        log.error(f"Erreur scraping HTML : {e}")

    return listings


# ── Email ─────────────────────────────────────────────────────────────────────
def send_email(new_listings: list[dict]):
    cfg = EMAIL_CONFIG
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎓 CROUS Orléans – {len(new_listings)} logement(s) disponible(s) !"
    msg["From"]    = cfg["sender"]
    msg["To"]      = cfg["recipient"]

    # Corps texte
    lines = [f"Bonjour !\n\n{len(new_listings)} nouveau(x) logement(s) CROUS détecté(s) à Orléans :\n"]
    for l in new_listings:
        lines.append(f"• {l['title']}")
        if l.get("address"):
            lines.append(f"  📍 {l['address']}")
        lines.append(f"  🔗 {l['url']}\n")
    lines.append(f"\nDétecté le {datetime.now().strftime('%d/%m/%Y à %H:%M')}")
    lines.append("\nBonne chance pour ta candidature !")
    text_body = "\n".join(lines)

    # Corps HTML
    items_html = "".join(
        f"""
        <tr>
          <td style="padding:14px 10px;border-bottom:1px solid #e5e7eb">
            <div style="font-weight:600;color:#1f2937">{l['title']}</div>
            {"<div style='color:#6b7280;font-size:13px;margin-top:4px'>📍 " + l['address'] + "</div>" if l.get('address') else ""}
            <div style="margin-top:8px">
              <a href="{l['url']}"
                 style="background:#4F46E5;color:white;padding:6px 14px;border-radius:6px;text-decoration:none;font-size:13px">
                Voir le logement →
              </a>
            </div>
          </td>
        </tr>"""
        for l in new_listings
    )

    html_body = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;color:#1f2937">
      <div style="background:linear-gradient(135deg,#4F46E5,#7C3AED);padding:24px;border-radius:10px 10px 0 0;text-align:center">
        <div style="font-size:40px">🎓</div>
        <h2 style="color:white;margin:8px 0 0">CROUS Orléans</h2>
        <p style="color:#e0e7ff;margin:4px 0 0">{len(new_listings)} nouveau(x) logement(s) disponible(s)</p>
      </div>
      <div style="padding:20px;background:#f9fafb;border-radius:0 0 10px 10px">
        <table width="100%" style="border-collapse:collapse">{items_html}</table>
        <div style="margin-top:20px;padding:16px;background:#ede9fe;border-radius:8px;border-left:4px solid #4F46E5">
          <strong>⚡ Agis vite !</strong> Les logements CROUS partent très rapidement.
        </div>
        <p style="color:#9ca3af;font-size:12px;margin-top:16px;text-align:center">
          Détecté le {datetime.now().strftime('%d/%m/%Y à %H:%M')} • Surveillant CROUS Orléans
        </p>
      </div>
    </body></html>"""

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html",  "utf-8"))

    try:
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as server:
            server.starttls()
            server.login(cfg["sender"], cfg["password"])
            server.sendmail(cfg["sender"], cfg["recipient"], msg.as_string())
        log.info(f"📧 Email envoyé ({len(new_listings)} logement(s))")
    except Exception as e:
        log.error(f"Erreur envoi email : {e}")
        raise


# ── Boucle principale ─────────────────────────────────────────────────────────
def run():
    log.info("═══════════════════════════════════════════")
    log.info("  🎓 Démarrage surveillance CROUS Orléans  ")
    log.info(f"  Vérification toutes les {INTERVALLE_MINUTES} minutes")
    log.info("═══════════════════════════════════════════")

    if os.environ.get("CI") == "true":
        # Run once for CI environments like GitHub Actions
        seen = load_seen()
        logements = fetch_logements()

        new = [l for l in logements if l["id"] not in seen]

        if new:
            log.info(f"🆕 {len(new)} nouveau(x) logement(s) !")
            send_email(new)
            for l in new:
                seen.add(l["id"])
            save_seen(seen)
        else:
            total = len(logements)
            log.info(f"Aucune nouveauté. ({total} logement(s) en ligne, déjà tous vus)")
        return

    interval = INTERVALLE_MINUTES * 60

    while True:
        seen = load_seen()
        logements = fetch_logements()

        new = [l for l in logements if l["id"] not in seen]

        if new:
            log.info(f"🆕 {len(new)} nouveau(x) logement(s) !")
            send_email(new)
            for l in new:
                seen.add(l["id"])
            save_seen(seen)
        else:
            total = len(logements)
            log.info(f"Aucune nouveauté. ({total} logement(s) en ligne, déjà tous vus)")

        log.info(f"⏳ Prochain check dans {INTERVALLE_MINUTES} min...\n")
        time.sleep(interval)


if __name__ == "__main__":
    run()