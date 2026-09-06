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
        print()
        print("⚠️ Erreur lecture {} : {}".format(path, error))
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


def normalize_spotify_url(url):
    if not url:
        return None

    url = url.strip()
    artist_id = extract_artist_id(url)

    if not artist_id:
        return None

    if not re.match(r"^https?://open\.spotify\.com/", url, flags=re.IGNORECASE):
        return None

    return url.split("?")[0].rstrip("/")


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
        print()
        print("⚠️ Erreur lecture JSON : {}".format(error))
        return {"artists": {}}


def save_database(database):
    temporary_file = JSON_FILE + ".tmp"

    try:
        with open(temporary_file, "w", encoding="utf-8") as file:
            json.dump(database, file, ensure_ascii=False, indent=4)

        os.replace(temporary_file, JSON_FILE)
        return True
    except Exception as error:
        print()
        print("❌ Erreur sauvegarde JSON : {}".format(error))
        return False


def extract_related_artists(html, source_artist_id, existing_ids):
    artists = {}

    if not html:
        return artists

    pattern = re.compile(
    r'href\s*=\s*["\']([^"\']*?/artist/[^"\']+)["\']',
    flags=re.IGNORECASE
    )

    matches = pattern.findall(html)

    print("Liens artistes détectés :", len(matches))
    print("Exemples :", matches[:5])

    print()
    print("Liens artistes détectés : {}".format(len(matches)))

    for raw_url in matches:
        url = raw_url.replace("\\/", "/").replace("&amp;", "&")

        if url.startswith("/"):
            url = SPOTIFY_DOMAIN + url

        artist_id = extract_artist_id(url)
        if not artist_id:
            continue

        if artist_id == source_artist_id:
            continue

        if artist_id in existing_ids or artist_id in artists:
            continue

        clean_url = build_artist_url(artist_id)
        timestamp = now()

        artists[artist_id] = {
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

    return artists


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

    print()
    print("------------------------------------------------------------")
    print("Analyse de l'artiste : {}".format(artist_id))
    print("URL : {}".format(artist_url))
    print("------------------------------------------------------------")

    try:
        page.goto(artist_url, wait_until="domcontentloaded", timeout=60000)
    except PlaywrightTimeoutError:
        print("⚠️ Timeout de chargement.")
    except Exception as error:
        print("❌ Erreur chargement artiste : {}".format(error))
        return existing_artist

    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        pass

    time.sleep(EXTRACTION_DELAY)

    try:
        html = page.content()
    except Exception as error:
        print("❌ Impossible de récupérer le HTML : {}".format(error))
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

    print()
    print("Nom : {}".format(artist["name"] if artist["name"] else "non trouvé"))
    print("Followers : {}".format(artist["followers"] if artist["followers"] is not None else "non trouvé"))
    print("Auditeurs mensuels : {}".format(artist["monthly_listeners"] if artist["monthly_listeners"] is not None else "non trouvé"))
    print("Popularité : {}".format(artist["popularity"] if artist["popularity"] is not None else "non trouvé"))
    print("Genres : {}".format(", ".join(artist["genres"]) if artist["genres"] else "aucun"))
    print("Images : {}".format(len(artist["images"])))

    return artist


def process_related_page(browser, database, related_url):
    source_artist_id = extract_artist_id(related_url)
    existing_ids = set(database["artists"].keys())

    print()
    print("=" * 70)
    print("RECHERCHE DES ARTISTES SIMILAIRES")
    print("=" * 70)

    related_page = browser.new_page()

    try:
        related_page.goto(
            related_url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        try:
            related_page.wait_for_load_state(
                "networkidle",
                timeout=30000
            )
        except Exception:
            pass

        time.sleep(EXTRACTION_DELAY)

        # Faire défiler plusieurs fois pour déclencher le chargement
        # des artistes dans l'interface Spotify.
        for _ in range(8):
            related_page.mouse.wheel(0, 1200)
            time.sleep(1)

        links = related_page.locator('a[href*="/artist/"]')
        hrefs = links.evaluate_all(
            "elements => elements.map(element => element.href)"
        )

        print()
        print("Liens artistes détectés dans le navigateur : {}".format(len(hrefs)))

        related_artists = {}

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

        print("Nouveaux artistes après filtrage : {}".format(
            len(related_artists)
        ))

    except Exception as error:
        print("❌ Erreur récupération artistes similaires : {}".format(error))
        related_page.close()
        return database

    related_page.close()

    if not related_artists:
        print()
        print("⚠️ Aucun nouvel artiste similaire trouvé.")
        return database

    detail_page = browser.new_page()

    try:
        total = len(related_artists)

        for current, artist_id in enumerate(related_artists, start=1):
            print()
            print("=" * 70)
            print("NOUVEL ARTISTE {}/{}".format(current, total))
            print("=" * 70)

            existing_artist = database["artists"].get(artist_id)
            artist_data = extract_full_artist(
                detail_page,
                artist_id,
                existing_artist
            )

            if not artist_data:
                print("⚠️ Impossible de récupérer cet artiste.")
                continue

            database["artists"][artist_id] = artist_data
            existing_ids.add(artist_id)
            save_database(database)

            print("✅ Nouvel artiste enregistré.")

    finally:
        detail_page.close()

    return database


def process_all_artists(browser, database, start_artist_number=1):
    artist_items = list(database["artists"].items())

    if not artist_items:
        print()
        print("Aucun artiste à traiter dans le JSON.")
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

        print()
        print("=" * 70)
        print("ARTISTE JSON {}/{}".format(position, total))
        print("=" * 70)

        artist_url = artist_data.get("url")
        related_url = build_related_url(artist_url)

        print()
        print("ID : {}".format(artist_id))
        print("URL : {}".format(artist_url if artist_url else "non trouvée"))
        print("RELATED : {}".format(related_url if related_url else "non trouvée"))

        monthly_listeners = artist_data.get("monthly_listeners")

        if monthly_listeners is None:
            print("⚠️ Auditeurs mensuels inconnus, artiste ignoré.")
            continue

        if monthly_listeners > MONTHLY_LISTENER_LIMIT:
            print("⚠️ {} auditeurs mensuels, au-dessus du seuil de {}. Artiste ignoré.".format(monthly_listeners, MONTHLY_LISTENER_LIMIT))
            continue

        if not related_url:
            print("⚠️ URL invalide, artiste ignoré.")
            continue

        database = process_related_page(browser, database, related_url)

    return database, last_position


def main():
    print()
    print("=" * 70)
    print("COLLECTEUR D'ARTISTES SIMILAIRES SPOTIFY")
    print("=" * 70)

    print()
    print("Seuil auditeurs mensuels : {}".format(MONTHLY_LISTENER_LIMIT))
    print("Délai avant extraction : {} seconde(s)".format(EXTRACTION_DELAY))
    print("Navigateur : Chromium headless")
    print("Fichier : {}".format(JSON_FILE))
    print("Taille du lot : {}".format(BATCH_SIZE))

    database = load_database()
    progress = load_progress()

    start_artist_number = progress.get("last_position", 0) + 1

    print()
    print("Artistes déjà présents : {}".format(len(database["artists"])))
    print("Reprise à partir de l'artiste numéro : {}".format(start_artist_number))

    if not database["artists"]:
        print()
        print("Aucun artiste dans le JSON.")
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
        print()
        print("Démarrage de Chromium...")

        try:
            browser = p.chromium.launch(headless=True)
        except Exception as error:
            print()
            print("❌ Impossible de démarrer Chromium.")
            print("➡️ Vérifie l'installation avec : python -m playwright install --with-deps chromium")
            print(error)
            write_log("Impossible de démarrer Chromium.", "error", {"error": str(error)})
            update_progress(status="error", message="Impossible de démarrer Chromium")
            return

        try:
            database, last_position = process_all_artists(browser, database, start_artist_number=start_artist_number)
        finally:
            print()
            print("Fermeture de Chromium...")
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

    print()
    print("=" * 70)
    print("LOT TERMINÉ")
    print("=" * 70)
    print()
    print("Dernière position traitée : {}".format(last_position))
    print("Prochain démarrage : {}".format(next_start))
    print("Total artistes dans le JSON : {}".format(len(database["artists"])))
    print("Fichier : {}".format(JSON_FILE))
    print()


if __name__ == "__main__":
    main()
