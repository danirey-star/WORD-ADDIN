# Control parental para Windows

Mide cuánto tiempo se usa cada juego (por día, semana y mes), envía los datos
al PC del padre por la red de casa y permite bloquear **internet**, **juegos**
o **apps concretas** al momento o por franjas horarias, además de poner un
**límite diario de juego**.

```
 PC del niño (agente, SYSTEM)              PC del padre (servidor)
 ┌─────────────────────────────┐   cada    ┌───────────────────────────┐
 │ mira procesos cada 5 s      │   30 s    │ guarda histórico (SQLite) │
 │ suma tiempo por juego       │ ────────▶ │ panel http://127.0.0.1:8765│
 │ aplica bloqueos             │ ◀──────── │ horarios, límites,        │
 │ panel del niño :8766 (solo  │  config   │ bloquear ahora            │
 │ lectura)                    │           └───────────────────────────┘
 └─────────────────────────────┘
```

- El agente **pregunta** al servidor (no al revés), así que el PC del niño no
  necesita abrir puertos. Si el PC del padre está apagado, el agente sigue
  midiendo y aplicando la última configuración, y envía los datos pendientes
  cuando vuelve a haber conexión.
- El niño tiene un acceso directo **"Mi tiempo de pantalla"** en el escritorio
  con sus propias gráficas y el tiempo de juego que le queda hoy (útil para que
  aprenda a autocontrolarse).
- Recibe un aviso 5 minutos antes de que empiece una franja de bloqueo o se le
  acabe el tiempo de juego.

## Instalación

Requisitos: Windows 10/11 en ambos PCs, en la misma red. Python 3.10+ (el
instalador lo instala con `winget` si falta).

Hay un único instalador, `Instalar.bat`, que sirve para los dos PCs. Al abrirlo
(pide permiso de administrador) pregunta qué quieres hacer en ese ordenador:
instalar el **control**, instalar el **agente** o **desinstalar**.

1. **PC del padre**: descarga esta carpeta, doble clic en `Instalar.bat` y
   elige **1 (Control)**. Al final muestra la **IP** del PC y un **token**.
   Apúntalos.
2. **PC del niño**: copia la misma carpeta, doble clic en `Instalar.bat`, elige
   **2 (Agente)** e introduce la IP y el token.
3. Abre en tu PC el acceso directo **"Control parental"** del escritorio
   (o http://127.0.0.1:8765). En menos de un minuto aparece el PC del niño.

**Muy importante:** la cuenta de Windows del niño debe ser de tipo *Usuario
estándar*. El agente se ejecuta como SYSTEM, así que un usuario estándar no
puede cerrarlo, cambiar su configuración ni quitar el bloqueo del firewall.
Si su cuenta es de administrador puede desactivarlo todo.

Consejo: reserva en el router una IP fija para tu PC (reserva DHCP); si
cambia, vuelve a ejecutar el instalador del agente con la nueva IP.

Para desinstalar: `Instalar.bat` → **3 (Desinstalar)** en el PC que quieras. En
el PC del padre pregunta si quieres conservar el histórico.

Sin menú (para automatizar), desde una consola de administrador:
`powershell -ExecutionPolicy Bypass -File instalar.ps1 -Modo agente -Servidor 192.168.1.50 -Token XXXX`
(`-Modo control` y `-Modo desinstalar` funcionan igual).

## Qué se mide

- **Juegos**: los de la lista (Fortnite, Minecraft, Roblox, LoL, Valorant,
  CS2, Rocket League, GTA V, EA FC…) y, automáticamente, cualquier programa
  instalado con Steam, Epic, Riot, Xbox, EA, Ubisoft o GOG (se usa el nombre de
  la carpeta del juego). Puedes añadir más en el panel → *Juegos y apps
  reconocidos* (`Nombre = ejecutable.exe`; el nombre del ejecutable se ve en
  Administrador de tareas → Detalles).
- **Otras apps**: Discord y navegadores por defecto (no cuentan para el límite
  de juego).
- **Ordenador**: tiempo con una sesión iniciada y desbloqueada.

Solo cuenta lo que se ejecuta en la sesión activa; con la pantalla bloqueada
(Win+L) o el PC suspendido no se suma tiempo. Se mide "juego abierto", no
"jugando activamente": si deja un juego abierto minimizado, cuenta.

## Bloqueos

| Tipo | Cómo funciona |
|---|---|
| Juegos | Cierra los procesos de juegos reconocidos mientras dure el bloqueo. |
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
- El agente solo cierra programas de la sesión del niño que estén en tus
  listas; nunca toca servicios del sistema ni del antivirus. Con internet
  bloqueado el antivirus sigue protegiendo, pero no se actualiza hasta que
  termina el bloqueo.

## Archivos

```
control_parental/
  rules.py      configuración, clasificación de juegos, franjas y límites (sin Windows)
  db.py         SQLite: segundos por minuto y programa; informes día/semana/mes
  platform.py   Windows: procesos, sesión activa, firewall, cerrar procesos, avisos
  agent.py      agente del PC del niño
  server.py     API del PC del padre
  web/dashboard.html  panel (sin dependencias externas: funciona sin internet)
run_agent.py / run_server.py   puntos de entrada
Instalar.bat / instalar.ps1    instalador único (tarea programada como SYSTEM al arrancar)
tests/                         pruebas (pytest)
```

Datos: `C:\ProgramData\ControlParental` (niño) y
`C:\ProgramData\ControlParentalServidor` (padre); ahí están también los logs.

## Desarrollo

```
pip install pytest
python -m pytest tests
python run_server.py        # en cualquier SO; fuera de Windows el agente usa una plataforma simulada
```
