# ig-cola

Cola de carruseles de [@crisodevelop](https://instagram.com/crisodevelop).

GitHub Actions corre todos los días a las 7:00 de RD. Si en `cola/<fecha de hoy>/`
hay un carrusel **aprobado por Criso**, lo publica en Instagram. Si no hay nada,
o está pendiente de aprobación, no hace nada.

- `cola/AAAA-MM-DD/1..N.jpg` y `caption.txt`: lo que va a salir.
- `APROBADO.json`: la aprobación, con una huella de las fotos y del caption. Si
  cambia algo después de aprobar, no se publica.
- `PUBLICADO.json`: lo escribe el workflow cuando sale el post.

Las fotos están aquí porque la API de Instagram no recibe archivos: solo URLs
públicas que Meta descarga.
