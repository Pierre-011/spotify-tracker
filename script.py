import json
import os
import re
import time
from datetime import datetime

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


JSON_FILE = "artistes.json"
PROGRESS_FILE = "progress.json"
LOG_FILE = "last-run.json"
EXTRACTION_DELAY = 2
SPOTIFY_DOMAIN = "https://open.spotify.com"
MONTHLY_LISTENER_LIMIT = 10000
BATCH_SIZE = 50


def log(message=""):
    print(message, flush=True)


def now():
    return datetime.now().isoformat(timespec="seconds")


def write_json_file(path, data):
    temporary_file = path + ".tmp"
    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)
    os.replace(temporary_file, path)


def load_json_file(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, dict):
            return data
    except Exception as error:
        log("⚠️ Erreur lecture {} : {}".format(path, error))
    return default


def load_progress():
    default = {
        "last_position": 0,
        "last_artist_id": None,
        "status": "idle",
        "updated_at": None,
        "batch_size": BATCH_SIZE
    }
    return load_json_file(PROGRESS_FILE, default)


def save_progress(data):
    write_json_file(PROGRESS_FILE, data)


def update_progress(
    status=None,
    last_position=None,
    last_artist_id=None,
    total=None,
    processed=None,
    current_artist=None,
    progress_pct=None,
    message=None
):
    progress = load_progress()

    if status is not None:
        progress["status"] = status
    if last_position is not None:
        progress["last_position"] = last_position
    if last_artist_id is not None:
        progress["last_artist_id"] = last_artist_id
    if total is not None:
        progress["total"] = total
    if processed is not None:
        progress["processed"] = processed
    if current_artist is not None:
        progress["current_artist"] = current_artist
    if progress_pct is not None:
        progress["progress_pct"] = progress_pct
    if message is not None:
        progress["message"] = message

    progress["batch_size"] = BATCH_SIZE
    progress["updated_at"] = now()

    save_progress(progress)


def write_log(message, level="info", extra=None):
    payload = {
        "timestamp": now(),
        "level": level,
        "message": message,
        "extra": extra or {}
    }
    try:
        write_json_file(LOG_FILE, payload)
    except Exception:
        pass


def extract_artist_id(url):
    if not url:
        return None

    url = url.strip()
    match = re.search(
        r"https?://open\.spotify\.com/(?:[^/]+/)*artist/([A-Za-z0-9]+)",
        url,
        flags=re.IGNORECASE
    )
    return match.group(1) if match else None


def build_artist_url(artist_id):
    return SPOTIFY_DOMAIN + "/intl-fr/artist/" + artist_id


def build_related_url(artist_url):
    if not artist_url:
        return None

    artist_url = artist_url.strip().rstrip("/")
    if artist_url.endswith("/related"):
        return artist_url
    return artist_url + "/related"


def load_database():
    if not os.path.exists(JSON_FILE):
        return {"artists": {}}

    try:
        with open(JSON_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            return {"artists": {}}

        if "artists" not in data or not isinstance(data["artists"], dict):
            data["artists"] = {}

        return data
    except Exception as error:
        log("⚠️ Erreur lecture JSON : {}".format(error))
        return {"artists": {}}


def save_database(database):
    temporary_file = JSON_FILE + ".tmp"

    try:
        with open(temporary_file, "w", encoding="utf-8") as file:
            json.dump(database, file, ensure_ascii=False, indent=4)

        os.replace(temporary_file, JSON_FILE)
        return True
    except Exception as error:
        log("❌ Erreur sauvegarde JSON : {}".format(error))
        return False


def find_integer_value(html, keys):
    for key in keys:
        pattern = r'"' + re.escape(key) + r'"\s*:\s*([0-9]+)'
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
    return None


def extract_artist_name(html, artist_id):
    escaped_id = re.escape(artist_id)
    patterns = [
        r'"uri"\s*:\s*"spotify:artist:' + escaped_id + r'".{0,5000}?"name"\s*:\s*"([^"]+)"',
        r'"id"\s*:\s*"' + escaped_id + r'".{0,5000}?"name"\s*:\s*"([^"]+)"',
        r"<title[^>]*>(.*?)</title>"
    ]

    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue

        name = match.group(1)
        name = re.sub(r"<[^>]+>", "", name)
        name = name.replace("&amp;", "&").replace("\\/", "/").strip()
        name = re.sub(r"\s*[|–-]\s*Spotify.*$", "", name, flags=re.IGNORECASE).strip()

        if name:
            return name

    return None


def extract_followers(html):
    value = find_integer_value(html, ["followers", "totalFollowers"])
    if value is not None:
        return value

    for pattern in [
        r'"followers"\s*:\s*\{\s*"total"\s*:\s*([0-9]+)',
        r'"totalFollowers"\s*:\s*([0-9]+)'
    ]:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass

    return None


def extract_popularity(html):
    return find_integer_value(html, ["popularity"])


def extract_genres(html, artist_id):
    genres = []
    escaped_id = re.escape(artist_id)

    pattern = r'"id"\s*:\s*"' + escaped_id + r'".{0,5000}?"genres"\s*:\s*\[([^\]]*)\]'
    match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)

    if match:
        genres = re.findall(r'"([^"]+)"', match.group(1))
        if genres:
            return genres

    matches = re.findall(r'"genres"\s*:\s*\[([^\]]*)\]', html, flags=re.IGNORECASE | re.DOTALL)
    for content in matches:
        values = re.findall(r'"([^"]+)"', content)
        for value in values:
            if value not in genres:
                genres.append(value)

    return genres


def extract_images(html, artist_id):
    images = []
    escaped_id = re.escape(artist_id)

    pattern = r'"id"\s*:\s*"' + escaped_id + r'".{0,10000}?"images"\s*:\s*\[(.*?)\]'
    match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)

    if not match:
        return images

    image_objects = re.findall(r'\{(.*?)\}', match.group(1), flags=re.DOTALL)

    for image_object in image_objects:
        url_match = re.search(r'"url"\s*:\s*"([^"]+)"', image_object)
        if not url_match:
            continue

        image_url = url_match.group(1).replace("\\/", "/")
        height_match = re.search(r'"height"\s*:\s*([0-9]+)', image_object)
        width_match = re.search(r'"width"\s*:\s*([0-9]+)', image_object)

        image = {
            "url": image_url,
            "height": int(height_match.group(1)) if height_match else None,
            "width": int(width_match.group(1)) if width_match else None
        }

        if image not in images:
            images.append(image)

    return images


def extract_monthly_listeners(page, html):
    try:
        body_text = page.locator("body").inner_text(timeout=10000)
    except Exception:
        body_text = ""

    for pattern in [
        r"([0-9][0-9\s.,]*)\s+auditeurs\s+mensuels",
        r"([0-9][0-9\s.,]*)\s+monthly\s+listeners",
        r"([0-9][0-9\s.,]*)\s+auditeurs\s+par\s+mois"
    ]:
        match = re.search(pattern, body_text, flags=re.IGNORECASE)
        if match:
            number = re.sub(r"[^\d]", "", match.group(1))
            if number:
                try:
                    return int(number)
                except ValueError:
                    pass

    for pattern in [
        r"([0-9][0-9\s.,]*)\s+auditeurs\s+mensuels",
        r"([0-9][0-9\s.,]*)\s+monthly\s+listeners"
    ]:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            number = re.sub(r"[^\d]", "", match.group(1))
            if number:
                try:
                    return int(number)
                except ValueError:
                    pass

    return None


def extract_full_artist(page, artist_id, existing_artist=None):
    artist_url = build_artist_url(artist_id)

    log()
    log("-" * 60)
    log("Analyse de l'artiste : {}".format(artist_id))
    log("URL : {}".format(artist_url))
    log("-" * 60)

    try:
        page.goto(artist_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_load_state("networkidle", timeout=30000)
    except PlaywrightTimeoutError:
        log("⚠️ Timeout de chargement.")
    except Exception as error:
        log("❌ Erreur chargement artiste : {}".format(error))
        return existing_artist

    time.sleep(EXTRACTION_DELAY)

    try:
        html = page.content()
    except Exception as error:
        log("❌ Impossible de récupérer le HTML : {}".format(error))
        return existing_artist

    name = extract_artist_name(html, artist_id)
    followers = extract_followers(html)
    popularity = extract_popularity(html)
    genres = extract_genres(html, artist_id)
    images = extract_images(html, artist_id)
    monthly_listeners = extract_monthly_listeners(page, html)

    timestamp = now()
    first_seen = existing_artist.get("first_seen") if existing_artist else None
    if not first_seen:
        first_seen = timestamp

    artist = {
        "id": artist_id,
        "name": name,
        "url": artist_url,
        "uri": "spotify:artist:" + artist_id,
        "followers": followers,
        "monthly_listeners": monthly_listeners,
        "popularity": popularity,
        "genres": genres,
        "images": images,
        "external_urls": {"spotify": artist_url},
        "first_seen": first_seen,
        "last_seen": timestamp
    }

    if existing_artist:
        for key in ["name", "followers", "monthly_listeners", "popularity"]:
            if artist[key] is None and existing_artist.get(key) is not None:
                artist[key] = existing_artist.get(key)

        if not artist["genres"]:
            artist["genres"] = existing_artist.get("genres", [])

        if not artist["images"]:
            artist["images"] = existing_artist.get("images", [])

    log("Nom : {}".format(artist["name"] if artist["name"] else "non trouvé"))
    log("Followers : {}".format(artist["followers"] if artist["followers"] is not None else "non trouvé"))
    log("Auditeurs mensuels : {}".format(artist["monthly_listeners"] if artist["monthly_listeners"] is not None else "non trouvé"))
    log("Popularité : {}".format(artist["popularity"] if artist["popularity"] is not None else "non trouvé"))
    log("Genres : {}".format(", ".join(artist["genres"]) if artist["genres"] else "aucun"))
    log("Images : {}".format(len(artist["images"])))

    return artist


def process_related_page(browser, database, related_url):
    source_artist_id = extract_artist_id(related_url)
    existing_ids = set(database["artists"].keys())

    log()
    log("=" * 70)
    log("RECHERCHE DES ARTISTES SIMILAIRES")
    log("=" * 70)

    related_page = browser.new_page(viewport={"width": 1600, "height": 2200})
    related_artists = {}

    try:
        log("Ouverture de la page related...")
        related_page.goto(related_url, wait_until="domcontentloaded", timeout=60000)

        try:
            related_page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass

        related_page.wait_for_timeout(4000)

        for i in range(12):
            log("Scroll related {} / 12".format(i + 1))
            related_page.mouse.wheel(0, 2500)
            related_page.wait_for_timeout(1000)

        try:
            related_page.locator('a[href*="/artist/"]').first.wait_for(timeout=15000)
        except Exception:
            pass

        hrefs = related_page.locator('a[href*="/artist/"]').evaluate_all(
            "els => [...new Set(els.map(e => e.href).filter(Boolean))]"
        )

        log("Liens artistes bruts détectés : {}".format(len(hrefs)))

        for url in hrefs:
            artist_id = extract_artist_id(url)

            if not artist_id:
                continue
            if artist_id == source_artist_id:
                continue
            if artist_id in existing_ids:
                continue
            if artist_id in related_artists:
                continue

            clean_url = build_artist_url(artist_id)
            timestamp = now()

            related_artists[artist_id] = {
                "id": artist_id,
                "name": None,
                "url": clean_url,
                "uri": "spotify:artist:" + artist_id,
                "followers": None,
                "monthly_listeners": None,
                "popularity": None,
                "genres": [],
                "images": [],
                "external_urls": {"spotify": clean_url},
                "first_seen": timestamp,
                "last_seen": timestamp
            }

        log("Nouveaux artistes après filtrage : {}".format(len(related_artists)))

        if not related_artists:
            log("⚠️ Aucun nouvel artiste similaire trouvé.")
            return database

        detail_page = browser.new_page(viewport={"width": 1600, "height": 2200})

        try:
            total = len(related_artists)

            for current, artist_id in enumerate(related_artists, start=1):
                log()
                log("=" * 70)
                log("NOUVEL ARTISTE {}/{}".format(current, total))
                log("=" * 70)

                existing_artist = database["artists"].get(artist_id)
                artist_data = extract_full_artist(detail_page, artist_id, existing_artist)

                if not artist_data:
                    log("⚠️ Impossible de récupérer cet artiste.")
                    continue

                database["artists"][artist_id] = artist_data
                existing_ids.add(artist_id)
                save_database(database)

                log("✅ Nouvel artiste enregistré.")

        finally:
            detail_page.close()

    except Exception as error:
        log("❌ Erreur récupération artistes similaires : {}".format(error))
        write_log("Erreur récupération artistes similaires", "error", {"error": str(error), "url": related_url})

    finally:
        related_page.close()

    return database


def process_all_artists(browser, database, start_artist_number=1):
    artist_items = list(database["artists"].items())

    if not artist_items:
        log()
        log("Aucun artiste à traiter dans le JSON.")
        return database, 0

    total = len(artist_items)
    processed = 0
    last_position = 0

    for position, (artist_id, artist_data) in enumerate(artist_items, start=1):
        if position < start_artist_number:
            continue

        if processed >= BATCH_SIZE:
            break

        processed += 1
        last_position = position

        update_progress(
            status="running",
            total=total,
            processed=processed,
            current_artist=artist_id,
            last_position=last_position,
            last_artist_id=artist_id,
            progress_pct=(last_position / total) * 100 if total else 0,
            message="Traitement en cours"
        )

        log()
        log("=" * 70)
        log("ARTISTE JSON {}/{}".format(position, total))
        log("=" * 70)

        artist_url = artist_data.get("url")
        related_url = build_related_url(artist_url)

        log("ID : {}".format(artist_id))
        log("URL : {}".format(artist_url if artist_url else "non trouvée"))
        log("RELATED : {}".format(related_url if related_url else "non trouvée"))

        monthly_listeners = artist_data.get("monthly_listeners")

        if monthly_listeners is None:
            log("⚠️ Auditeurs mensuels inconnus, artiste ignoré.")
            continue

        if monthly_listeners > MONTHLY_LISTENER_LIMIT:
            log("⚠️ {} auditeurs mensuels, au-dessus du seuil de {}. Artiste ignoré.".format(monthly_listeners, MONTHLY_LISTENER_LIMIT))
            continue

        if not related_url:
            log("⚠️ URL invalide, artiste ignoré.")
            continue

        database = process_related_page(browser, database, related_url)

    return database, last_position


def main():
    log()
    log("=" * 70)
    log("COLLECTEUR D'ARTISTES SIMILAIRES SPOTIFY")
    log("=" * 70)

    log()
    log("Seuil auditeurs mensuels : {}".format(MONTHLY_LISTENER_LIMIT))
    log("Délai avant extraction : {} seconde(s)".format(EXTRACTION_DELAY))
    log("Navigateur : Chromium headless")
    log("Fichier : {}".format(JSON_FILE))
    log("Taille du lot : {}".format(BATCH_SIZE))

    database = load_database()
    progress = load_progress()

    start_artist_number = progress.get("last_position", 0) + 1

    log()
    log("Artistes déjà présents : {}".format(len(database["artists"])))
    log("Reprise à partir de l'artiste numéro : {}".format(start_artist_number))

    if not database["artists"]:
        log()
        log("Aucun artiste dans le JSON.")
        write_log("Aucun artiste à traiter.", "warning")
        update_progress(status="idle", message="Aucun artiste à traiter", total=0, processed=0, progress_pct=0)
        return

    write_log("Démarrage du scraping.")
    update_progress(
        status="running",
        total=len(database["artists"]),
        processed=0,
        current_artist=None,
        last_position=start_artist_number - 1,
        last_artist_id=None,
        message="Démarrage du lot"
    )

    with sync_playwright() as p:
        log()
        log("Démarrage de Chromium...")

        try:
            browser = p.chromium.launch(headless=True)
        except Exception as error:
            log()
            log("❌ Impossible de démarrer Chromium.")
            log("➡️ Vérifie l'installation avec : python -m playwright install --with-deps chromium")
            log(str(error))
            write_log("Impossible de démarrer Chromium.", "error", {"error": str(error)})
            update_progress(status="error", message="Impossible de démarrer Chromium")
            return

        try:
            database, last_position = process_all_artists(browser, database, start_artist_number=start_artist_number)
        finally:
            log()
            log("Fermeture de Chromium...")
            try:
                browser.close()
            except Exception:
                pass

    save_database(database)

    next_start = last_position + 1
    update_progress(
        status="paused",
        current_artist=None,
        last_position=last_position,
        last_artist_id=None,
        message="Lot terminé",
        progress_pct=(last_position / len(database["artists"]) * 100) if database["artists"] else 0
    )

    write_log("Lot terminé.", "success", {
        "last_position": last_position,
        "next_start": next_start,
        "batch_size": BATCH_SIZE,
        "total_artists": len(database["artists"])
    })

    log()
    log("=" * 70)
    log("LOT TERMINÉ")
    log("=" * 70)
    log()
    log("Dernière position traitée : {}".format(last_position))
    log("Prochain démarrage : {}".format(next_start))
    log("Total artistes dans le JSON : {}".format(len(database["artists"])))
    log("Fichier : {}".format(JSON_FILE))
    log()


if __name__ == "__main__":
    main()
