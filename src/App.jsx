import { useRef, useState } from "react"

function App() {
  const fileInputRef = useRef(null)
  const videoRef = useRef(null)
  const streamRef = useRef(null)

  const [image, setImage] = useState(null)
  const [cameraOpen, setCameraOpen] = useState(false)
  const [cameraFiles, setCameraFiles] = useState([])
  const [cameraPreviews, setCameraPreviews] = useState([])
  const [scanning, setScanning] = useState(false)
  const [scanned, setScanned] = useState(false)
  const [error, setError] = useState("")

  const [data, setData] = useState({
    mrp: "Not detected",
    quantity: "Not detected",
    manufacturer: "Not detected",
    batch: "Not detected",
    mfgDate: "Not detected",
    expiry: "Not detected",
    consumerCare: "Not detected",
  })

  const [compliance, setCompliance] = useState(null)
  const [activePage, setActivePage] = useState("inspect")
  const [history, setHistory] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem("packsure_history") || "[]")
    } catch {
      return []
    }
  })

  // ---------------- CAMERA ----------------

  const startCamera = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: 1920, min: 1280 },
          height: { ideal: 1080, min: 720 },
          frameRate: { ideal: 30, min: 15 },
        },
        audio: false,
      })

      // Ask the browser for the highest practical camera resolution.
      // Some webcams may ignore these values; in that case we keep the
      // best resolution the device can provide.
      const videoTrack = stream.getVideoTracks()[0]
      if (videoTrack?.applyConstraints) {
        try {
          await videoTrack.applyConstraints({
            width: { ideal: 1920 },
            height: { ideal: 1080 },
          })
        } catch (constraintError) {
          console.warn("High-resolution camera constraints were not applied:", constraintError)
        }
      }

      streamRef.current = stream
      setCameraOpen(true)

      setTimeout(() => {
        if (videoRef.current) {
          videoRef.current.srcObject = stream
        }
      }, 100)
    } catch (error) {
      alert("Camera access was denied or is not available.")
      console.error(error)
    }
  }

  const stopCamera = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => {
        track.stop()
      })

      streamRef.current = null
    }

    setCameraOpen(false)
  }

  // ---------------- INSPECTION ----------------

  const inspectPackage = async (files, previewURL) => {
    if (!files || files.length === 0) return

    setImage(previewURL || URL.createObjectURL(files[0]))
    setScanning(true)
    setScanned(false)
    setError("")
    setCompliance(null)

    try {
      const formData = new FormData()

      files.forEach((file) => {
        formData.append("files", file)
      })

      const response = await fetch(
        "http://127.0.0.1:8000/inspect",
        {
          method: "POST",
          body: formData,
        }
      )

      const result = await response.json()

      if (!response.ok || !result.success) {
        throw new Error(
          result.message || "Package inspection failed."
        )
      }

      const extracted = result.data?.extracted || {}
      const complianceResult = result.data?.compliance || null

      setData({
        mrp: extracted.mrp || "Not detected",
        quantity: extracted.net_quantity || "Not detected",
        manufacturer: extracted.manufacturer || "Not detected",
        batch: extracted.batch_number || "Not detected",
        mfgDate: extracted.manufacturing_date || "Not detected",
        expiry: extracted.expiry_or_best_before || "Not detected",
        consumerCare: extracted.consumer_care || "Not detected",
      })

      setCompliance(complianceResult)
      saveHistory(extracted, complianceResult)
      setScanning(false)
      setScanned(true)

    } catch (error) {
      console.error("Inspection error:", error)
      setScanning(false)
      setScanned(false)
      setError(
        error.message ||
          "Could not connect to the PackSure backend."
      )
    }
  }

  // ---------------- CAMERA CAPTURE + ENHANCEMENT ----------------

  const captureImage = async () => {
    const video = videoRef.current

    if (!video || !video.videoWidth || !video.videoHeight) {
      setError("Camera is not ready yet. Please wait a moment and try again.")
      return
    }

    if (cameraFiles.length >= 2) {
      setError("You can capture a maximum of 2 images per inspection.")
      return
    }

    const sourceWidth = video.videoWidth
    const sourceHeight = video.videoHeight

    const minimumWidth = 1920
    const scale = Math.max(1, minimumWidth / sourceWidth)

    const canvas = document.createElement("canvas")
    canvas.width = Math.round(sourceWidth * scale)
    canvas.height = Math.round(sourceHeight * scale)

    const context = canvas.getContext("2d", {
      willReadFrequently: true,
    })

    if (!context) {
      setError("Could not process the camera image.")
      return
    }

    context.imageSmoothingEnabled = true
    context.imageSmoothingQuality = "high"
    context.filter = "contrast(1.12) brightness(1.04) saturate(1.02)"

    context.drawImage(
      video,
      0,
      0,
      canvas.width,
      canvas.height
    )

    context.filter = "none"

    try {
      const imageData = context.getImageData(
        0,
        0,
        canvas.width,
        canvas.height
      )

      const pixels = imageData.data
      const contrast = 1.08
      const midpoint = 128

      for (let i = 0; i < pixels.length; i += 4) {
        pixels[i] = Math.max(0, Math.min(255, (pixels[i] - midpoint) * contrast + midpoint))
        pixels[i + 1] = Math.max(0, Math.min(255, (pixels[i + 1] - midpoint) * contrast + midpoint))
        pixels[i + 2] = Math.max(0, Math.min(255, (pixels[i + 2] - midpoint) * contrast + midpoint))
      }

      context.putImageData(imageData, 0, 0)
    } catch (enhanceError) {
      console.warn("Camera enhancement pass skipped:", enhanceError)
    }

    const capturedImage = canvas.toDataURL("image/jpeg", 0.94)

    canvas.toBlob(
      (blob) => {
        if (!blob) {
          setError("Could not capture the image.")
          return
        }

        const file = new File(
          [blob],
          `camera-package-${cameraFiles.length + 1}.jpg`,
          { type: "image/jpeg" }
        )

        setCameraFiles((previous) => [...previous, file])
        setCameraPreviews((previous) => [...previous, capturedImage])
        setImage(capturedImage)
        setError("")
      },
      "image/jpeg",
      0.94
    )
  }

  const inspectCameraImages = async () => {
    if (!cameraFiles.length) return

    stopCamera()

    await inspectPackage(
      cameraFiles,
      cameraPreviews[0]
    )
  }

  // ---------------- UPLOAD ----------------

  const handleImageUpload = async (event) => {
    const file = event.target.files[0]

    if (!file) return

    const imageURL = URL.createObjectURL(file)

    await inspectPackage(
      [file],
      imageURL
    )

    event.target.value = ""
  }

  // ---------------- RESET ----------------

  const resetInspection = () => {
    setImage(null)
    setScanned(false)
    setScanning(false)
    setError("")
    setCompliance(null)
    setCameraFiles([])
    setCameraPreviews([])

    setData({
      mrp: "Not detected",
      quantity: "Not detected",
      manufacturer: "Not detected",
      batch: "Not detected",
      mfgDate: "Not detected",
      expiry: "Not detected",
      consumerCare: "Not detected",
    })
  }

  // ---------------- COMPLIANCE HELPERS ----------------

  const checkNames = {
    mrp_declaration: "MRP Declaration",
    net_quantity: "Net Quantity",
    manufacturer_details: "Manufacturer Details",
    batch_number: "Batch Number",
    manufacturing_date: "Manufacturing Date",
    expiry_best_before: "Expiry / Best Before",
    consumer_care: "Consumer Care",
    character_size_readability:
      "Character Size & Readability",
  }

  const getCheckClass = (status) => {
    if (status === "PASS") {
      return "check passed"
    }

    return "check review-check"
  }

  const getCheckIcon = (status) => {
    if (status === "PASS") {
      return "✓"
    }

    return "!"
  }

  // ---------------- CONFIDENCE HELPERS ----------------

  const confidenceFor = (key) => {
    if (!compliance?.checks?.[key]) return null
    return compliance.checks[key].confidence_percent ?? null
  }

  const confidenceStyle = (percent) => {
    if (percent === null || percent === undefined) return {}

    return {
      display: "inline-block",
      marginTop: "6px",
      padding: "3px 8px",
      borderRadius: "999px",
      fontSize: "10px",
      fontWeight: 700,
      letterSpacing: "0.2px",
      background: percent < 80 ? "rgba(245, 158, 11, 0.14)" : "rgba(34, 197, 94, 0.12)",
      color: percent < 80 ? "#f59e0b" : "#4ade80",
      border: percent < 80 ? "1px solid rgba(245, 158, 11, 0.35)" : "1px solid rgba(34, 197, 94, 0.3)",
    }
  }

  const averageConfidence = () => {
    const values = Object.values(compliance?.checks || {})
      .map((check) => check.confidence_percent)
      .filter((value) => typeof value === "number")

    if (!values.length) return null

    return Math.round(
      values.reduce((sum, value) => sum + value, 0) / values.length
    )
  }

  // ---------------- HISTORY + PDF ----------------

  const saveHistory = (extracted, complianceResult) => {
    const entry = {
      id: Date.now(),
      time: new Date().toLocaleString(),
      manufacturer: extracted.manufacturer || "Unknown manufacturer",
      quantity: extracted.net_quantity || "Quantity not detected",
      mrp: extracted.mrp || "MRP not detected",
      status: complianceResult?.overall_status || "NEEDS REVIEW",
      summary: complianceResult?.overall_message || "Inspection completed.",
    }

    setHistory((previous) => {
      const updated = [entry, ...previous].slice(0, 20)
      localStorage.setItem("packsure_history", JSON.stringify(updated))
      return updated
    })
  }

  const clearHistory = () => {
    localStorage.removeItem("packsure_history")
    setHistory([])
  }

  const generatePDF = () => {
    window.print()
  }

  // ---------------- RENDER ----------------

  return (
    <div className="app">

      {/* HEADER */}

      <header className="header">

        <div className="brand">

          <div className="logo">
            <span>Pack</span>Sure
          </div>

          <div className="tagline">
            Let’s verify.
          </div>

        </div>

        <nav className="nav">
          <button
            className={activePage === "inspect" ? "active" : ""}
            onClick={() => setActivePage("inspect")}
          >
            Inspect
          </button>
          <button
            className={activePage === "history" ? "active" : ""}
            onClick={() => setActivePage("history")}
          >
            History
          </button>
          <button
            className={activePage === "rules" ? "active" : ""}
            onClick={() => setActivePage("rules")}
          >
            Rules
          </button>
        </nav>

      </header>


      {/* MAIN */}

      <main className="main">

        {activePage === "inspect" ? (

          <>
            {/* INTRO */}

            <section className="intro">

          <h1>
            Let’s verify this package.
          </h1>

          <p>
            Scan or upload a package image to check its
            declarations and compliance.
          </p>

        </section>


        {/* INSPECTION */}

        <section className="inspection">


          {/* LEFT */}

          <div className="upload-box">

            {cameraOpen ? (

              <div className="camera-container">

                <div style={{ position: "relative" }}>
                  <video
                    ref={videoRef}
                    autoPlay
                    playsInline
                    className="camera-preview"
                  />

                  <div
                    style={{
                      position: "absolute",
                      inset: "12% 10%",
                      border: "2px dashed rgba(255,255,255,0.75)",
                      borderRadius: "12px",
                      pointerEvents: "none",
                    }}
                  >
                    <div
                      style={{
                        position: "absolute",
                        left: "50%",
                        bottom: "-38px",
                        transform: "translateX(-50%)",
                        width: "max-content",
                        maxWidth: "90%",
                        padding: "6px 10px",
                        borderRadius: "8px",
                        background: "rgba(0,0,0,0.65)",
                        color: "white",
                        fontSize: "11px",
                        textAlign: "center",
                      }}
                    >
                      {cameraFiles.length === 0
                        ? "Capture the full package first. Move close only if needed."
                        : "Optional: move closer and capture the small-text panel."}
                    </div>
                  </div>
                </div>

                {cameraPreviews.length > 0 && (
                  <div
                    style={{
                      display: "flex",
                      gap: "8px",
                      marginTop: "42px",
                      justifyContent: "center",
                    }}
                  >
                    {cameraPreviews.map((preview, index) => (
                      <div key={index} style={{ position: "relative" }}>
                        <img
                          src={preview}
                          alt={`Captured panel ${index + 1}`}
                          style={{
                            width: "76px",
                            height: "56px",
                            objectFit: "cover",
                            borderRadius: "6px",
                            border: "1px solid rgba(255,255,255,0.25)",
                          }}
                        />
                        <span
                          style={{
                            position: "absolute",
                            left: "4px",
                            bottom: "3px",
                            fontSize: "9px",
                            color: "white",
                            background: "rgba(0,0,0,0.65)",
                            padding: "2px 4px",
                            borderRadius: "3px",
                          }}
                        >
                          {index === 0 ? "Full" : "Detail"}
                        </span>
                      </div>
                    ))}
                  </div>
                )}

                <div className="camera-actions">

                  {cameraFiles.length < 2 && (
                    <button
                      className="capture-button"
                      onClick={captureImage}
                    >
                      {cameraFiles.length === 0
                        ? "Capture Full Package"
                        : "Capture Detail"}
                    </button>
                  )}

                  {cameraFiles.length > 0 && (
                    <button
                      className="scan-button"
                      onClick={inspectCameraImages}
                    >
                      Inspect {cameraFiles.length} Image{cameraFiles.length > 1 ? "s" : ""}
                    </button>
                  )}

                  <button
                    className="cancel-button"
                    onClick={stopCamera}
                  >
                    Cancel
                  </button>

                </div>

              </div>


            ) : image ? (

              <div className="image-container">

                <img
                  src={image}
                  alt="Package"
                  className="package-image"
                />

                {scanning && (
                  <div className="scanning-message">

                    <div className="loader"></div>

                    <h3>
                      Reading the package…
                    </h3>

                    <p>
                      Extracting declarations and checking the label.
                    </p>

                  </div>
                )}

                {!scanning && scanned && (
                  <div className="image-actions">

                    <button
                      className="scan-button"
                      onClick={startCamera}
                    >
                      Scan Again
                    </button>

                    <button
                      className="upload-button"
                      onClick={() =>
                        fileInputRef.current.click()
                      }
                    >
                      Upload Another
                    </button>

                  </div>
                )}

              </div>


            ) : (

              <>

                <div className="upload-icon">
                  +
                </div>

                <h2>
                  Scan your package
                </h2>

                <p>
                  Capture the package using your camera
                  or upload an existing image.
                </p>

                <div className="scan-actions">

                  <button
                    className="scan-button"
                    onClick={startCamera}
                  >
                    Scan Package
                  </button>

                  <button
                    className="upload-button"
                    onClick={() =>
                      fileInputRef.current.click()
                    }
                  >
                    Upload Image
                  </button>

                </div>

              </>

            )}


            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              onChange={handleImageUpload}
              hidden
            />

          </div>


          {/* RIGHT — RESULT */}

          <div className="result-box">

            <div className="result-header">

              <span>
                Inspection Result
              </span>

              <span
                className={`status ${
                  scanning
                    ? "checking"
                    : scanned
                    ? compliance?.overall_status ===
                      "COMPLIANT"
                      ? "passed"
                      : "review"
                    : "waiting"
                }`}
              >
                {scanning
                  ? "CHECKING"
                  : scanned
                  ? compliance?.overall_status ||
                    "NEEDS REVIEW"
                  : "READY"}
              </span>

            </div>


            <div className="result-content">

              {!image && (
                <>
                  <h2>
                    Ready when you are.
                  </h2>

                  <p>
                    Scan or upload a package image to
                    begin the compliance inspection.
                  </p>
                </>
              )}


              {scanning && (
                <>
                  <h2>
                    Reading the package…
                  </h2>

                  <p>
                    PackSure is extracting information
                    and checking the declarations.
                  </p>
                </>
              )}


              {!scanning && scanned && (
                <>
                  <h2>
                    {compliance?.overall_status ===
                    "COMPLIANT"
                      ? "Everything checked out."
                      : "Here’s what we found."}
                  </h2>

                  <p>
                    {compliance?.overall_message ||
                      "The package has been scanned and the extracted information is ready for review."}
                  </p>
                </>
              )}

              {error && (
                <p className="error-message">
                  {error}
                </p>
              )}

            </div>

          </div>

        </section>


        {/* EXTRACTED INFORMATION */}

        {scanned && !scanning && (

          <section className="details-section">

            <div className="section-heading">

              <h2>
                Extracted Information
              </h2>

              <span>
                OCR + Information Extraction
              </span>

            </div>


            <div className="details-grid">

              <div className="detail-card">
                <span>MRP</span>
                <strong>{data.mrp}</strong>
                {confidenceFor("mrp_declaration") !== null && (
                  <small style={confidenceStyle(confidenceFor("mrp_declaration"))}>
                    {confidenceFor("mrp_declaration")}% confidence
                  </small>
                )}
              </div>

              <div className="detail-card">
                <span>Net Quantity</span>
                <strong>{data.quantity}</strong>
                {confidenceFor("net_quantity") !== null && (
                  <small style={confidenceStyle(confidenceFor("net_quantity"))}>
                    {confidenceFor("net_quantity")}% confidence
                  </small>
                )}
              </div>

              <div className="detail-card">
                <span>Manufacturer</span>
                <strong>{data.manufacturer}</strong>
                {confidenceFor("manufacturer_details") !== null && (
                  <small style={confidenceStyle(confidenceFor("manufacturer_details"))}>
                    {confidenceFor("manufacturer_details")}% confidence
                  </small>
                )}
              </div>

              <div className="detail-card">
                <span>Batch Number</span>
                <strong>{data.batch}</strong>
                {confidenceFor("batch_number") !== null && (
                  <small style={confidenceStyle(confidenceFor("batch_number"))}>
                    {confidenceFor("batch_number")}% confidence
                  </small>
                )}
              </div>

              <div className="detail-card">
                <span>Manufacturing Date</span>
                <strong>{data.mfgDate}</strong>
                {confidenceFor("manufacturing_date") !== null && (
                  <small style={confidenceStyle(confidenceFor("manufacturing_date"))}>
                    {confidenceFor("manufacturing_date")}% confidence
                  </small>
                )}
              </div>

              <div className="detail-card">
                <span>Expiry / Best Before</span>
                <strong>{data.expiry}</strong>
                {confidenceFor("expiry_best_before") !== null && (
                  <small style={confidenceStyle(confidenceFor("expiry_best_before"))}>
                    {confidenceFor("expiry_best_before")}% confidence
                  </small>
                )}
              </div>

              <div className="detail-card">
                <span>Consumer Care</span>
                <strong>{data.consumerCare}</strong>
                {confidenceFor("consumer_care") !== null && (
                  <small style={confidenceStyle(confidenceFor("consumer_care"))}>
                    {confidenceFor("consumer_care")}% confidence
                  </small>
                )}
              </div>

            </div>

          </section>

        )}


        {/* COMPLIANCE */}

        {scanned && !scanning && compliance && (

          <section className="compliance-section">

            <div className="section-heading">

              <h2>
                Compliance Check
              </h2>

              <span>
                Legal Metrology Rule Engine
              </span>

            </div>


            {/* REAL BACKEND CHECKS */}

            <div className="checks">

              {Object.entries(
                compliance.checks || {}
              ).map(([key, check]) => (

                <div
                  key={key}
                  className={getCheckClass(
                    check.status
                  )}
                >

                  <span>
                    {getCheckIcon(
                      check.status
                    )}
                  </span>

                  <div>

                    <strong>
                      {checkNames[key] || key}
                    </strong>

                    <p>
                      {check.message}
                    </p>

                    {check.confidence_percent !== undefined && (
                      <small style={confidenceStyle(check.confidence_percent)}>
                        {check.confidence_percent}% extraction confidence
                      </small>
                    )}

                  </div>

                </div>

              ))}

            </div>


            {/* FINAL RESULT */}

            <div className="final-result">

              <div>

                <span className="final-label">
                  Overall Result
                </span>

                <h3>
                  {compliance.overall_message}
                </h3>

                <p>
                  {compliance.summary?.passed || 0} passed
                  {" • "}
                  {compliance.summary?.failed || 0} failed
                  {" • "}
                  {compliance.summary?.review || 0} review
                </p>

                {averageConfidence() !== null && (
                  <small
                    style={{
                      display: "block",
                      marginTop: "6px",
                      opacity: 0.72,
                    }}
                  >
                    Average extraction confidence: {averageConfidence()}%
                  </small>
                )}

              </div>

              <span
                className={`status ${
                  compliance.overall_status ===
                  "COMPLIANT"
                    ? "passed"
                    : "review"
                }`}
              >
                {compliance.overall_status}
              </span>

            </div>


            <div className="report-actions">
              <button
                className="scan-button"
                onClick={generatePDF}
              >
                Generate PDF Report
              </button>

              <button
                className="reset-button"
                onClick={resetInspection}
              >
                Inspect Another Package
              </button>
            </div>

          </section>

        )}

          </>
        ) : activePage === "history" ? (

          <section className="details-section">
            <div className="section-heading">
              <h2>Inspection History</h2>
              <span>Previous inspections</span>
            </div>

            {history.length === 0 ? (
              <div className="check">
                <div>
                  <strong>No inspections yet.</strong>
                  <p>Your completed package inspections will appear here.</p>
                </div>
                <button className="scan-button" onClick={() => setActivePage("inspect")}>
                  Inspect a Package
                </button>
              </div>
            ) : (
              <>
                <div className="checks">
                  {history.map((item) => (
                    <div className="check passed" key={item.id}>
                      <span>✓</span>
                      <div>
                        <strong>{item.manufacturer}</strong>
                        <p>{item.quantity} • {item.mrp}</p>
                        <p>{item.time} • {item.status}</p>
                      </div>
                    </div>
                  ))}
                </div>

                <button className="clear-history" onClick={clearHistory}>
                  Clear History
                </button>
              </>
            )}
          </section>

        ) : (

          <section className="details-section">
            <div className="section-heading">
              <h2>Legal Metrology Rules</h2>
              <span>Key declaration checks</span>
            </div>

            <div className="checks">
              <div className="check">
                <span>✓</span>
                <div>
                  <strong>MRP Declaration</strong>
                  <p>Checks that the maximum retail price declaration is detected and readable.</p>
                </div>
              </div>

              <div className="check">
                <span>✓</span>
                <div>
                  <strong>Net Quantity</strong>
                  <p>Checks the declared net quantity of the packaged commodity.</p>
                </div>
              </div>

              <div className="check">
                <span>✓</span>
                <div>
                  <strong>Manufacturer Details</strong>
                  <p>Checks for the required manufacturer or responsible entity details.</p>
                </div>
              </div>

              <div className="check">
                <span>✓</span>
                <div>
                  <strong>Batch Number</strong>
                  <p>Checks whether the batch or lot identification is declared.</p>
                </div>
              </div>

              <div className="check">
                <span>✓</span>
                <div>
                  <strong>Manufacturing Date</strong>
                  <p>Checks for the manufacturing date declaration.</p>
                </div>
              </div>

              <div className="check">
                <span>✓</span>
                <div>
                  <strong>Expiry / Best Before</strong>
                  <p>Checks the expiry or best-before declaration where applicable.</p>
                </div>
              </div>

              <div className="check">
                <span>✓</span>
                <div>
                  <strong>Consumer Care</strong>
                  <p>Checks for consumer care/contact information.</p>
                </div>
              </div>

              <div className="check">
                <span>✓</span>
                <div>
                  <strong>Character Size & Readability</strong>
                  <p>Flags cases requiring visual verification of label readability and character size.</p>
                </div>
              </div>
            </div>
          </section>
        )}

      </main>

    </div>
  )
}

export default App

 
