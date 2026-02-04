/* global Office, Word */

// Estado global del glosario
let glosario = {};

// Cargar glosario al iniciar
Office.onReady((info) => {
    if (info.host === Office.HostType.Word) {
        document.getElementById("btnReview").onclick = revisarDocumentoConGlosario;
        document.getElementById("btnAddWord").onclick = iniciarAgregarPalabra;
        document.getElementById("btnAssignActor").onclick = iniciarAsignarActor;
        document.getElementById("btnImportGlossary").onclick = () => {
            document.getElementById("fileInput").click();
        };
        
        document.getElementById("fileInput").onchange = importarGlosarioDesdeArchivo;
        
        document.getElementById("btnConfirmAdd").onclick = confirmarAgregarPalabra;
        document.getElementById("btnCancelAdd").onclick = cancelarModal;
        
        document.getElementById("btnConfirmActor").onclick = confirmarAsignarActor;
        document.getElementById("btnCancelActor").onclick = cancelarModal;
        
        // Intentar cargar desde storage primero, luego desde JSON
        cargarGlosarioDesdeStorage().then(cargado => {
            if (!cargado) {
                cargarGlosario();
            } else {
                mostrarEstado("Glosario cargado desde almacenamiento", "success");
            }
        });
    }
});

// ============================================================
//   FUNCIÓN: Cargar glosario desde JSON
// ============================================================
async function cargarGlosario() {
    try {
        // Intentar cargar desde la misma ruta que el HTML
        const baseUrl = window.location.href.substring(0, window.location.href.lastIndexOf('/') + 1);
        const response = await fetch(baseUrl + 'glosario.json');
        if (response.ok) {
            const data = await response.json();
            procesarGlosarioDesdeArray(data);
            // Guardar en storage también
            await guardarGlosario();
            mostrarEstado("Glosario cargado correctamente", "success");
        } else {
            mostrarEstado("No se encontró el archivo de glosario. Se creará uno nuevo.", "error");
        }
    } catch (error) {
        console.error("Error cargando glosario:", error);
        mostrarEstado("Error al cargar glosario. Se trabajará sin glosario previo.", "error");
    }
}

// ============================================================
//   FUNCIÓN: Procesar glosario desde array
// ============================================================
function procesarGlosarioDesdeArray(data) {
    glosario = {};
    if (Array.isArray(data)) {
        data.forEach(item => {
            if (item.palabra && item.pronunciacion) {
                const palabraBase = item.palabra.toLowerCase();
                glosario[palabraBase] = {
                    palabra: item.palabra,
                    pronunciacion: item.pronunciacion,
                    textoCompleto: `${item.palabra} (${item.pronunciacion})`
                };
            }
        });
    }
}

// ============================================================
//   FUNCIÓN: Guardar glosario en JSON
// ============================================================
async function guardarGlosario() {
    try {
        // Convertir diccionario a array ordenado
        const array = Object.values(glosario)
            .map(item => ({
                palabra: item.palabra,
                pronunciacion: item.pronunciacion
            }))
            .sort((a, b) => a.palabra.localeCompare(b.palabra, 'es'));
        
        // Guardar usando Office Storage (persistencia local)
        const storage = OfficeRuntime.storage;
        await storage.setItem("glosario", JSON.stringify(array));
        
        mostrarEstado("Glosario guardado", "success");
    } catch (error) {
        console.error("Error guardando glosario:", error);
        mostrarEstado("Error al guardar glosario", "error");
    }
}

// ============================================================
//   FUNCIÓN: Cargar glosario desde Office Storage
// ============================================================
async function cargarGlosarioDesdeStorage() {
    try {
        const storage = OfficeRuntime.storage;
        const data = await storage.getItem("glosario");
        if (data) {
            const array = JSON.parse(data);
            procesarGlosarioDesdeArray(array);
            return true;
        }
    } catch (error) {
        console.error("Error cargando desde storage:", error);
    }
    return false;
}

// ============================================================
//   FUNCIÓN: Importar glosario desde archivo (TXT o JSON)
// ============================================================
async function importarGlosarioDesdeArchivo(event) {
    const file = event.target.files[0];
    if (!file) return;
    
    const reader = new FileReader();
    
    reader.onload = async (e) => {
        try {
            const contenido = e.target.result;
            const nombreArchivo = file.name.toLowerCase();
            
            if (nombreArchivo.endsWith('.json')) {
                // Formato JSON
                const data = JSON.parse(contenido);
                procesarGlosarioDesdeArray(data);
            } else if (nombreArchivo.endsWith('.txt')) {
                // Formato TXT original: "Palabra (pronunciación)"
                const lineas = contenido.split(/\r?\n/);
                const arrayGlosario = [];
                
                lineas.forEach(linea => {
                    const lineaTrim = linea.trim();
                    if (lineaTrim && lineaTrim.indexOf(" (") > 0) {
                        const partes = lineaTrim.split(" (");
                        if (partes.length === 2) {
                            const palabra = partes[0].trim();
                            const pronunciacion = partes[1].replace(/\)$/, "").trim();
                            if (palabra && pronunciacion) {
                                arrayGlosario.push({
                                    palabra: palabra,
                                    pronunciacion: pronunciacion
                                });
                            }
                        }
                    }
                });
                
                procesarGlosarioDesdeArray(arrayGlosario);
            } else {
                mostrarEstado("Formato de archivo no soportado. Use .txt o .json", "error");
                return;
            }
            
            // Guardar en storage
            await guardarGlosario();
            mostrarEstado(`Glosario importado: ${Object.keys(glosario).length} palabras`, "success");
            
            // Limpiar input
            event.target.value = "";
        } catch (error) {
            console.error("Error importando glosario:", error);
            mostrarEstado("Error al importar glosario: " + error.message, "error");
        }
    };
    
    reader.readAsText(file, 'UTF-8');
}

// ============================================================
//   MACRO: Revisar documento con glosario
//   - No duplica pronunciaciones
//   - No toca nombres de personaje (*Nombre)
// ============================================================
async function revisarDocumentoConGlosario() {
    mostrarEstado("Revisando documento...", "");
    
    // Cargar glosario desde storage si no está cargado
    if (Object.keys(glosario).length === 0) {
        await cargarGlosarioDesdeStorage();
    }
    
    if (Object.keys(glosario).length === 0) {
        mostrarEstado("No hay glosario disponible", "error");
        return;
    }
    
    try {
        await Word.run(async (context) => {
            const body = context.document.body;
            body.load("text");
            
            await context.sync();
            
            let reemplazos = 0;
            
            // Procesar cada palabra del glosario
            for (const clave in glosario) {
                const item = glosario[clave];
                const palabraBuscar = item.palabra;
                const textoReemplazo = item.textoCompleto;
                
                // Buscar todas las ocurrencias
                const searchResults = body.search(palabraBuscar, {
                    matchCase: false,
                    matchWholeWord: true
                });
                
                context.load(searchResults, "items");
                await context.sync();
                
                // Procesar cada resultado
                for (let i = 0; i < searchResults.items.length; i++) {
                    const range = searchResults.items[i];
                    
                    // Verificar que no tenga ya paréntesis (evitar duplicados)
                    range.load("text");
                    await context.sync();
                    
                    if (range.text.indexOf("(") === -1) {
                        // Verificar que no esté precedido por '*'
                        const startPos = range.start;
                        if (startPos > 0) {
                            const prevRange = range.getRange("Before");
                            prevRange.load("text");
                            await context.sync();
                            
                            const textoAnterior = prevRange.text;
                            if (!textoAnterior.endsWith("*")) {
                                // Reemplazar
                                range.text = textoReemplazo;
                                reemplazos++;
                            }
                        } else {
                            // Si está al inicio, reemplazar directamente
                            range.text = textoReemplazo;
                            reemplazos++;
                        }
                    }
                }
            }
            
            await context.sync();
            mostrarEstado(`Revisión completa. ${reemplazos} reemplazos realizados.`, "success");
        });
    } catch (error) {
        console.error("Error en revisarDocumentoConGlosario:", error);
        mostrarEstado("Error al revisar documento: " + error.message, "error");
    }
}

// ============================================================
//   MACRO: Añadir palabra nueva al glosario
//   - Evita duplicados
//   - Guarda ordenado
// ============================================================
let palabraSeleccionada = "";

async function iniciarAgregarPalabra() {
    try {
        await Word.run(async (context) => {
            const selection = context.document.getSelection();
            selection.load("text");
            
            await context.sync();
            
            const texto = selection.text.trim().replace(/\r\n/g, "").replace(/\n/g, "");
            
            if (!texto) {
                mostrarEstado("Selecciona una palabra", "error");
                return;
            }
            
            // Verificar que no tenga ya paréntesis
            if (texto.indexOf("(") > -1) {
                mostrarEstado("La palabra seleccionada ya tiene pronunciación", "error");
                return;
            }
            
            palabraSeleccionada = texto;
            
            // Verificar si ya existe en el glosario
            const palabraBase = texto.toLowerCase();
            if (glosario[palabraBase]) {
                // Ya existe, reemplazar en documento
                await reemplazarPalabraEnDocumento(glosario[palabraBase].textoCompleto);
                mostrarEstado("Palabra ya existía. Reemplazada en documento.", "success");
                return;
            }
            
            // Mostrar modal para pedir pronunciación
            document.getElementById("modalWordText").textContent = `Palabra: ${texto}`;
            document.getElementById("inputPronunciation").value = "";
            document.getElementById("modalAddWord").style.display = "flex";
            document.getElementById("inputPronunciation").focus();
        });
    } catch (error) {
        console.error("Error en iniciarAgregarPalabra:", error);
        mostrarEstado("Error: " + error.message, "error");
    }
}

async function confirmarAgregarPalabra() {
    const pronunciacion = document.getElementById("inputPronunciation").value.trim();
    
    if (!pronunciacion) {
        mostrarEstado("Debes introducir una pronunciación", "error");
        return;
    }
    
    if (!palabraSeleccionada) {
        cancelarModal();
        return;
    }
    
    // Cargar glosario desde storage si no está cargado
    if (Object.keys(glosario).length === 0) {
        await cargarGlosarioDesdeStorage();
    }
    
    const palabraBase = palabraSeleccionada.toLowerCase();
    const textoCompleto = `${palabraSeleccionada} (${pronunciacion})`;
    
    // Agregar al glosario
    glosario[palabraBase] = {
        palabra: palabraSeleccionada,
        pronunciacion: pronunciacion,
        textoCompleto: textoCompleto
    };
    
    // Guardar glosario
    await guardarGlosario();
    
    // Reemplazar en documento
    await reemplazarPalabraEnDocumento(textoCompleto);
    
    cancelarModal();
    mostrarEstado("Palabra añadida y reemplazada en el documento", "success");
}

async function reemplazarPalabraEnDocumento(textoReemplazo) {
    try {
        await Word.run(async (context) => {
            const body = context.document.body;
            const searchResults = body.search(palabraSeleccionada, {
                matchCase: false,
                matchWholeWord: true
            });
            
            context.load(searchResults, "items");
            await context.sync();
            
            for (let i = 0; i < searchResults.items.length; i++) {
                searchResults.items[i].text = textoReemplazo;
            }
            
            await context.sync();
        });
    } catch (error) {
        console.error("Error reemplazando palabra:", error);
        throw error;
    }
}

// ============================================================
//   MACRO: Asignar actor a personaje seleccionado
//   - Detecta personaje con regex
//   - Rellena tabla final
// ============================================================
let personajeSeleccionado = "";

async function iniciarAsignarActor() {
    try {
        await Word.run(async (context) => {
            const selection = context.document.getSelection();
            selection.load("text");
            
            await context.sync();
            
            const linea = selection.text;
            
            // Buscar patrón *Nombre con regex
            const regex = /\*([A-Za-zÁÉÍÓÚÜÑ][A-Za-z0-9ÁÉÍÓÚÜÑñ]+)/;
            const match = linea.match(regex);
            
            if (!match) {
                mostrarEstado("No se detectó personaje (*Nombre)", "error");
                return;
            }
            
            personajeSeleccionado = match[1];
            
            // Verificar que hay tablas
            const tables = context.document.body.tables;
            tables.load("items");
            await context.sync();
            
            if (tables.items.length === 0) {
                mostrarEstado("No hay tabla en el documento", "error");
                return;
            }
            
            // Mostrar modal
            document.getElementById("modalCharacterText").textContent = `Personaje: ${personajeSeleccionado}`;
            document.getElementById("inputActor").value = "";
            document.getElementById("modalAssignActor").style.display = "flex";
            document.getElementById("inputActor").focus();
        });
    } catch (error) {
        console.error("Error en iniciarAsignarActor:", error);
        mostrarEstado("Error: " + error.message, "error");
    }
}

async function confirmarAsignarActor() {
    const nombreActor = document.getElementById("inputActor").value.trim();
    
    if (!nombreActor) {
        mostrarEstado("Debes introducir un nombre de actor", "error");
        return;
    }
    
    if (!personajeSeleccionado) {
        cancelarModal();
        return;
    }
    
    try {
        await Word.run(async (context) => {
            const tables = context.document.body.tables;
            tables.load("items");
            await context.sync();
            
            if (tables.items.length === 0) {
                mostrarEstado("No hay tabla en el documento", "error");
                cancelarModal();
                return;
            }
            
            // Obtener última tabla
            const ultimaTabla = tables.items[tables.items.length - 1];
            const rows = ultimaTabla.rows;
            rows.load("items");
            await context.sync();
            
            let encontrado = false;
            
            // Buscar fila con el personaje
            for (let i = 0; i < rows.items.length; i++) {
                const row = rows.items[i];
                const cells = row.cells;
                cells.load("items");
                await context.sync();
                
                if (cells.items.length >= 2) {
                    const celdaPersonaje = cells.items[0];
                    celdaPersonaje.body.load("text");
                    await context.sync();
                    
                    const textoPersonaje = celdaPersonaje.body.text.trim();
                    // Remover caracteres de fin de celda
                    const nombrePersonaje = textoPersonaje.replace(/[\r\n\u0007]/g, "");
                    
                    if (nombrePersonaje.toLowerCase() === personajeSeleccionado.toLowerCase()) {
                        // Encontrado, escribir actor en segunda celda
                        const celdaActor = cells.items[1];
                        celdaActor.body.clear();
                        celdaActor.body.insertText(nombreActor, Word.InsertLocation.replace);
                        encontrado = true;
                        break;
                    }
                }
            }
            
            await context.sync();
            
            if (encontrado) {
                mostrarEstado(`Actor asignado a ${personajeSeleccionado}`, "success");
            } else {
                mostrarEstado("No se encontró el personaje en la tabla", "error");
            }
            
            cancelarModal();
        });
    } catch (error) {
        console.error("Error en confirmarAsignarActor:", error);
        mostrarEstado("Error: " + error.message, "error");
        cancelarModal();
    }
}

// ============================================================
//   FUNCIONES AUXILIARES
// ============================================================

function cancelarModal() {
    document.getElementById("modalAddWord").style.display = "none";
    document.getElementById("modalAssignActor").style.display = "none";
    palabraSeleccionada = "";
    personajeSeleccionado = "";
}

function mostrarEstado(mensaje, tipo) {
    const statusDiv = document.getElementById("statusMessage");
    const statusSection = document.querySelector(".status");
    
    statusDiv.textContent = mensaje;
    statusSection.className = "status " + (tipo || "");
    
    if (tipo === "success" || tipo === "error") {
        statusSection.classList.add(tipo);
    }
}
