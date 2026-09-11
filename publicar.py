#!/usr/bin/env python3
"""Publica en Instagram el carrusel que toque hoy. Lo corre GitHub Actions cada
mañana (.github/workflows/publicar.yml), no la Mac: así sale aunque la Mac esté
apagada.

La API de Instagram no programa: publica en el momento en que se la llama. Por
eso el workflow arranca a las 6:30 de RD y este script espera hasta las 7:00 en
punto (GitHub suele disparar los cron con retraso, nunca antes).

NADA SALE SIN LA APROBACIÓN DE CRISO. Las reglas, en este orden:
  1. Si no existe cola/<hoy>/, no hace nada y sale en verde.
  2. Si ya tiene PUBLICADO.json, no hace nada: relanzarlo no duplica el post.
  3. Si no tiene APROBADO.json, no publica: está pendiente de aprobación.
  4. Si tiene APROBADO.json pero la huella no coincide con las fotos y el
     caption de ahora, FALLA a propósito, para que GitHub avise por correo:
     alguien tocó la entrada después de que Criso la aprobara.

Modo prueba (PRUEBA=true): hace todo menos el media_publish. Los contenedores
que crea no se publican y Meta los borra solos a las 24 horas.

Las fotos no se suben a Instagram: la API solo acepta URLs públicas que Meta
descarga. Se sirven desde jsDelivr fijadas al commit, así una foto no puede
cambiar entre la comprobación y la publicación.
"""
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
COLA = RAIZ / "cola"
RD = timezone(timedelta(hours=-4))       # Santo Domingo, sin horario de verano
HORA = (7, 0)                            # hora de RD a la que sale el post
API = "https://graph.instagram.com"
AVISO_DIAS = 7                           # falla si al token le queda menos


# ---------------------------------------------------------------- la cola
def fotos(d: Path):
    """Las láminas en orden: 1.jpg, 2.jpg, ... 10.jpg (orden numérico, no de texto)."""
    return sorted(d.glob("*.jpg"), key=lambda p: int(p.stem))


def huella(d: Path) -> str:
    """Huella de lo que se aprobó: cada foto con su nombre, y el caption.

    La usa también publicar_instagram.py (la importa de aquí), así que aprobar
    y comprobar calculan exactamente lo mismo.
    """
    h = hashlib.sha256()
    for f in fotos(d):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    h.update((d / "caption.txt").read_bytes())
    return h.hexdigest()


def estado(d: Path) -> str:
    if (d / "PUBLICADO.json").exists():
        return "publicado"
    ap = d / "APROBADO.json"
    if not ap.exists():
        return "pendiente"
    if json.loads(ap.read_text())["huella"] != huella(d):
        return "cambiado"
    return "aprobado"


# ------------------------------------------------------------- instagram
def _pedir(metodo, ruta, **datos):
    import requests
    datos["access_token"] = os.environ["IG_TOKEN"]
    url = f"{API}/{ruta}"
    if metodo == "GET":
        r = requests.get(url, params=datos, timeout=60)
    else:
        r = requests.post(url, data=datos, timeout=60)
    try:
        j = r.json()
    except ValueError:
        j = {"texto": r.text[:300]}
    if r.status_code != 200 or "error" in j:
        # nunca imprimir el token
        sys.exit(f"Instagram rechazó {metodo} {ruta.split('?')[0]} "
                 f"({r.status_code}): {json.dumps(j, ensure_ascii=False)}")
    return j


def urls_publicas(fecha, lista):
    """URLs de jsDelivr fijadas al commit, comprobadas una por una.

    Si jsDelivr no responde, prueba con raw.githubusercontent.com. Antes de darle
    una URL a Meta se comprueba que devuelve 200 y un JPEG de verdad, porque si
    falla la descarga Meta solo dice «media download has failed».
    """
    import requests
    repo = os.environ["GITHUB_REPOSITORY"]
    sha = os.environ["GITHUB_SHA"]
    bases = [f"https://cdn.jsdelivr.net/gh/{repo}@{sha}/cola/{fecha}",
             f"https://raw.githubusercontent.com/{repo}/{sha}/cola/{fecha}"]
    salida = []
    for f in lista:
        buena = None
        for base in bases:
            url = f"{base}/{f.name}"
            for intento in range(4):
                try:
                    r = requests.get(url, timeout=30)
                    tipo = r.headers.get("Content-Type", "")
                    if r.status_code == 200 and tipo.startswith("image/jpeg") \
                            and r.content == f.read_bytes():
                        buena = url
                        break
                except requests.RequestException:
                    pass
                time.sleep(5 * (intento + 1))
            if buena:
                break
        if not buena:
            sys.exit(f"ninguna URL pública sirve {f.name} bien; no se llama a Meta")
        salida.append(buena)
        print(f"  ✓ {f.name} → {buena}")
    return salida


def esperar_hasta(fecha):
    objetivo = datetime.strptime(fecha, "%Y-%m-%d").replace(
        hour=HORA[0], minute=HORA[1], tzinfo=RD)
    falta = (objetivo - datetime.now(RD)).total_seconds()
    if falta > 2 * 3600:
        sys.exit(f"faltan {falta/3600:.1f} h para las {HORA[0]}:{HORA[1]:02d}; "
                 "demasiado pronto, no se espera tanto")
    if falta > 0:
        print(f"  esperando {falta/60:.0f} min hasta las {HORA[0]}:{HORA[1]:02d} (RD)…")
        time.sleep(falta)


def renovar():
    """Renueva el token (dura 60 días) y lo guarda de vuelta en GitHub.

    Con Instagram Login el token NO se renueva solo por usarlo: hay que pedirlo
    con refresh_access_token, y si pasan 60 días sin hacerlo muere y toca volver
    a sacarlo a mano. Se renueva cuando ya lleva 10 días puesto (Meta exige al
    menos 24 horas) y se escribe con `gh`, que para tocar los secrets necesita
    el GH_PAT (un token fino, solo para este repo). Sin él, no se renueva y el
    aviso de caducidad de token_vigente() es lo que queda.
    """
    import subprocess
    import requests
    exp = os.environ.get("IG_TOKEN_EXPIRA", "").strip()
    ahora = datetime.now(timezone.utc)
    if exp and (datetime.fromisoformat(exp) - ahora).days > 50:
        return
    if not os.environ.get("GH_TOKEN"):
        print("  (sin GH_PAT: el token no se renueva solo)")
        return
    r = requests.get(f"{API}/refresh_access_token", timeout=60, params={
        "grant_type": "ig_refresh_token", "access_token": os.environ["IG_TOKEN"]})
    j = r.json()
    if r.status_code != 200 or "access_token" not in j:
        print(f"  ✗ no se pudo renovar el token ({r.status_code}): "
              f"{json.dumps(j.get('error', j), ensure_ascii=False)}")
        return
    nuevo = j["access_token"]
    print(f"::add-mask::{nuevo}")          # que no salga nunca en el log
    expira = (ahora + timedelta(seconds=int(j.get("expires_in", 5184000)))).isoformat(
        timespec="seconds")
    repo = os.environ["GITHUB_REPOSITORY"]
    subprocess.run(["gh", "secret", "set", "IG_TOKEN", "--repo", repo],
                   input=nuevo, text=True, check=True, capture_output=True)
    subprocess.run(["gh", "variable", "set", "IG_TOKEN_EXPIRA", "--repo", repo,
                    "--body", expira], check=True, capture_output=True)
    os.environ["IG_TOKEN"] = nuevo
    os.environ["IG_TOKEN_EXPIRA"] = expira
    print(f"  ✓ token renovado, vale hasta {expira[:10]}")


def quien_soy():
    """Comprueba el token contra Instagram y devuelve el id de la cuenta."""
    j = _pedir("GET", "me", fields="user_id,username")
    print(f"  cuenta: @{j.get('username')} ({j.get('user_id')})")
    return str(j.get("user_id") or "")


def token_vigente():
    exp = os.environ.get("IG_TOKEN_EXPIRA", "").strip()
    if not exp:
        print("  (sin IG_TOKEN_EXPIRA: no se puede avisar de la caducidad)")
        return
    quedan = (datetime.fromisoformat(exp) - datetime.now(timezone.utc)).days
    if quedan < AVISO_DIAS:
        sys.exit(f"al token de Instagram le quedan {quedan} días. Renuévalo: "
                 "python3 pipeline/publicar_instagram.py token")
    print(f"  token vigente, le quedan {quedan} días")


def publicar(d: Path, fecha, prueba, uid):
    lista = fotos(d)
    caption = (d / "caption.txt").read_text().strip()
    urls = urls_publicas(fecha, lista)

    hijos = []
    for f, url in zip(lista, urls):
        hijos.append(_pedir("POST", f"{uid}/media", image_url=url,
                            is_carousel_item="true")["id"])
    padre = _pedir("POST", f"{uid}/media", media_type="CAROUSEL",
                   children=",".join(hijos), caption=caption)["id"]
    print(f"  contenedor del carrusel: {padre} ({len(hijos)} láminas)")

    for _ in range(30):                   # hasta 5 minutos
        st = _pedir("GET", padre, fields="status_code")["status_code"]
        if st == "FINISHED":
            break
        if st in ("ERROR", "EXPIRED"):
            sys.exit(f"el contenedor quedó en {st}")
        time.sleep(10)
    else:
        sys.exit("el contenedor no llegó a FINISHED en 5 minutos")
    print("  contenedor listo (FINISHED)")

    if prueba:
        print("  PRUEBA: todo bien hasta aquí. No se publica nada.")
        return

    esperar_hasta(fecha)
    mid = _pedir("POST", f"{uid}/media_publish", creation_id=padre)["id"]
    # PUBLICADO.json se escribe YA, antes de pedir nada más: si lo que sigue
    # fallara y el archivo no existiera, relanzar el job publicaría dos veces.
    registro = {"media_id": mid,
                "publicado": datetime.now(RD).isoformat(timespec="seconds")}
    (d / "PUBLICADO.json").write_text(json.dumps(registro, indent=2) + "\n")
    try:
        registro["permalink"] = _pedir("GET", mid, fields="permalink").get("permalink", "")
        (d / "PUBLICADO.json").write_text(json.dumps(registro, indent=2) + "\n")
    except SystemExit as e:
        print(f"  (publicado, pero no se pudo leer el enlace: {e})")
    print(f"  ✓ PUBLICADO {registro.get('permalink', mid)}")


def main():
    prueba = os.environ.get("PRUEBA", "").lower() == "true"
    fecha = os.environ.get("FECHA", "").strip() or datetime.now(RD).strftime("%Y-%m-%d")
    d = COLA / fecha
    print(f"cola/{fecha} · {'PRUEBA' if prueba else 'de verdad'}")

    # Todos los días, haya post o no: así el token no se muere en una semana
    # sin publicaciones.
    renovar()
    if prueba:
        quien_soy()

    if not d.is_dir():
        print("  no hay nada en la cola para hoy")
        return
    e = estado(d)
    if e == "publicado":
        print("  ya estaba publicado; no se repite")
        return
    if e == "cambiado":
        sys.exit("la entrada cambió después de aprobarse (la huella no coincide). "
                 "No sale. Hay que volver a aprobarla.")
    if e == "pendiente" and not prueba:
        print("  pendiente de aprobación de Criso: no sale")
        return
    if e == "pendiente":
        print("  pendiente de aprobación (en prueba se revisa igual, sin publicar)")

    token_vigente()
    uid = os.environ.get("IG_USER_ID", "").strip() or quien_soy()
    publicar(d, fecha, prueba, uid)


if __name__ == "__main__":
    main()
