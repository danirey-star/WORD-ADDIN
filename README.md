# Control parental para Windows

Mide cuánto tiempo se usa cada juego (por día, semana y mes), envía los datos
al PC del padre por la red de casa y permite bloquear **internet**, **juegos**
o **apps concretas** al momento o por franjas horarias, además de poner un
**límite diario de juego**.

```
 PC de tu hijo (agente, SYSTEM)              PC del padre (servidor)
 ┌─────────────────────────────┐   cada    ┌───────────────────────────┐
 │ mira procesos cada 5 s      │   30 s    │ guarda histórico (SQLite) │
 │ suma tiempo por juego       │ ────────▶ │ panel http://127.0.0.1:8765│
 │ aplica bloqueos             │ ◀──────── │ horarios, límites,        │
 │ panel del hijo :8766 (solo  │  config   │ bloquear ahora            │
 │ lectura)                    │           └───────────────────────────┘
 └─────────────────────────────┘
```

- El agente **pregunta** al servidor (no al revés), así que el PC de tu hijo no
  necesita abrir puertos. Si el PC del padre está apagado, el agente sigue
  midiendo y aplicando la última configuración, y envía los datos pendientes
  cuando vuelve a haber conexión.
- Tu hijo tiene un acceso directo **"Mi tiempo de pantalla"** en el escritorio
  con sus propias gráficas y el tiempo de juego que le queda hoy (útil para que
  aprenda a autocontrolarse).
- Recibe un aviso 5 minutos antes de que empiece una franja de bloqueo o se le
  acabe el tiempo de juego.

## Instalación

Requisitos: Windows 10/11 en ambos PCs, en la misma red. No hay que instalar
nada más: cada PC lleva su propio Python portátil oficial (python.org) dentro
de la carpeta del programa, sin tocar Windows. Tu PC necesita internet la
primera vez para descargarlo; el PC de tu hijo no lo necesita para instalar.

La carpeta entera solo hace falta en **tu** PC. Al PC de tu hijo se lleva un
único archivo.

1. **Tu PC**: descarga esta carpeta, doble clic en `Instalar.bat` (pide
   permiso de administrador) y elige **1**. Instala el control y deja en tu
   escritorio **`Instalar cliente.bat`**, que ya lleva dentro el
   programa, la IP de tu PC y el token.
2. **PC de tu hijo**: copia solo ese archivo (USB, carpeta compartida…) y dale
   doble clic. No pregunta nada; si tu PC está encendido lo encuentra solo. Al
   terminar, **borra el archivo** de su PC (lleva el token dentro).
3. Abre en tu PC el acceso directo **"Control parental"** del escritorio
   (o http://127.0.0.1:8765). En menos de un minuto aparece el PC de tu hijo.

Para actualizarlo o quitarlo del PC de tu hijo, vuelve a abrir ese mismo archivo
allí: si ya está instalado ofrece **Actualizar** o **Desinstalar**.

**Muy importante:** la cuenta de Windows de tu hijo debe ser de tipo *Usuario
estándar*. El agente se ejecuta como SYSTEM, así que un usuario estándar no
puede cerrarlo, cambiar su configuración ni quitar el bloqueo del firewall.
Si su cuenta es de administrador puede desactivarlo todo.

Consejo: reserva en el router una IP fija para tu PC (reserva DHCP); si
cambia, vuelve a ejecutar `Instalar.bat` → **1** en tu PC (conserva el token y
el histórico) y abre el nuevo `Instalar cliente.bat` en su PC.

Para desinstalar de tu PC: `Instalar.bat` → **2 (Desinstalar)**. Pregunta si
quieres conservar el histórico.

**Actualizaciones:** el PC de tu hijo se actualiza solo. Cada vez que vuelves a
ejecutar `Instalar.bat` → **1** en tu PC con una versión nueva del programa, su
agente la descarga de tu PC en menos de un minuto (comprobando que llega
entera), la instala y se reinicia. No hay que tocar su PC.

Sin menú (para automatizar), desde una consola de administrador:
`powershell -ExecutionPolicy Bypass -File instalar.ps1 -Modo control`
(o `-Modo desinstalar`).

## Qué se mide

- **Juegos**: los de la lista (Fortnite, Minecraft, Roblox, LoL, Valorant,
  CS2, Rocket League, GTA V, EA FC…) y, automáticamente, cualquier programa
  instalado con Steam, Epic, Riot, Xbox, EA, Ubisoft o GOG (se usa el nombre de
  la carpeta del juego). Puedes añadir más en el panel → *Juegos y apps
  reconocidos* (`Nombre = ejecutable.exe`; el nombre del ejecutable se ve en
  Administrador de tareas → Detalles).
- **Redes y vídeo**: YouTube, TikTok, Instagram, Facebook, X, Snapchat, Twitch
  y Reddit, en el navegador (se reconocen por el título de la pestaña) o en su
  app. Solo cuenta el tiempo que están **delante**, no el que están abiertas.
  Se pueden añadir más webs o apps en el panel (`Nombre = web.com, app.exe`).
- **Otras apps**: Discord, WhatsApp, Telegram y navegadores (no cuentan para
  ningún límite).
- **Ordenador (uso general)**: tiempo con la sesión desbloqueada **y en uso**.
  Si pasa más de 5 minutos sin tocar teclado ni ratón deja de contar, salvo
  que haya un juego abierto o un vídeo delante.

En el panel, la gráfica **Uso general** reparte ese tiempo entre juegos, redes
y vídeo y el resto del ordenador; debajo, **Por programa** da el detalle. En
"Ahora" se ve además qué ventana está usando en ese momento, aunque no esté en
ninguna lista.

**Alarmas para autocontrolarse:** en su panel ("Mi tiempo de pantalla") tu
hijo puede ponerse avisos: dentro de X minutos, o al llevar X minutos de juego o
de redes y vídeo en el día, con un mensaje propio. Suenan y salen por encima de
todo aunque haya cerrado la página o esté jugando. Tú las ves en tu panel, pero
no cambian ningún bloqueo ni límite.

Para ver qué ventana usa, el agente abre un pequeño ayudante sin ventana
(`ayudante.py`) en la sesión de tu hijo; si lo cierra, se vuelve a abrir solo.
Con la pantalla bloqueada (Win+L) o el PC suspendido no se suma tiempo. Los
juegos cuentan mientras están abiertos, aunque estén minimizados.

## Bloqueos

| Tipo | Cómo funciona |
|---|---|
| Juegos | Cierra los procesos de juegos reconocidos mientras dure el bloqueo. |
| Redes y vídeo | Cierra sus apps, bloquea sus webs en el archivo `hosts` de Windows y, si tiene una abierta delante, cierra esa pestaña. Tiene su propio límite diario. |
| Apps | Cierra los ejecutables indicados (p. ej. `discord.exe`). |
| Internet | Regla del Firewall de Windows que bloquea todo el tráfico saliente a internet. La red de casa (incluido tu PC) sigue accesible, así que los datos y las órdenes siguen llegando. |

Se pueden activar al momento (30 min, 1 h, 2 h, 4 h o indefinido) o por
franjas horarias (p. ej. L–J y D de 22:00 a 07:00 sin internet). Si la hora de
fin es menor que la de inicio, la franja cruza la medianoche.

Limitaciones a conocer:
- Las conexiones ya abiertas en el momento de bloquear internet pueden
  mantenerse hasta que se cierren; combínalo con el bloqueo de juegos si
  quieres cortar una partida online.
- Los avisos usan `msg.exe`, que no existe en algunas ediciones Home; en ese
  caso el bloqueo funciona igual pero sin aviso previo.
- El panel del padre solo se abre desde el propio PC del padre.

## Antivirus y firewall

- **Microsoft Defender**: los instaladores excluyen del análisis la carpeta del
  programa, para que no lo ralentice ni lo ponga en cuarentena. Como la carpeta
  solo la pueden modificar administradores, la exclusión no abre una puerta a
  otros programas.
- **Antivirus de otra marca** (Avast, Kaspersky, ESET…): el instalador lo
  detecta y te avisa; añade a mano la carpeta a sus exclusiones.
- **Firewall de otra marca**: si sustituye al Firewall de Windows, el bloqueo
  de internet puede no funcionar y tendrás que permitir el puerto 8765 en el PC
  del padre. Los bloqueos de juegos y apps no dependen del firewall.
- El agente solo cierra programas de la sesión de tu hijo que estén en tus
  listas; nunca toca servicios del sistema ni del antivirus. Con internet
  bloqueado el antivirus sigue protegiendo, pero no se actualiza hasta que
  termina el bloqueo.

## Archivos

```
control_parental/
  rules.py      configuración, clasificación de juegos, franjas y límites (sin Windows)
  db.py         SQLite: segundos por minuto y programa; informes día/semana/mes
  platform.py   Windows: procesos, sesión activa, firewall, cerrar procesos, avisos
  agent.py      agente del PC de tu hijo
  server.py     API del PC del padre
  update.py     actualización del agente por red desde el PC del padre
  web/dashboard.html  panel (sin dependencias externas: funciona sin internet)
run_agent.py / run_server.py   puntos de entrada
ayudante.py                    corre en la sesión de tu hijo: ventana en primer plano e inactividad
Instalar.bat / instalar.ps1    instalador de tu PC (tarea programada como SYSTEM al arrancar)
agente_instalador.ps1          plantilla con la que se genera "Instalar cliente.bat"
tests/                         pruebas (pytest)
```

Datos: `C:\ProgramData\ControlParental` (hijo) y
`C:\ProgramData\ControlParentalServidor` (padre); ahí están también los logs.

## Desarrollo

```
pip install pytest
python -m pytest tests
python run_server.py        # en cualquier SO; fuera de Windows el agente usa una plataforma simulada
```
