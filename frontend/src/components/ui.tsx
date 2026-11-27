import { useEffect, useRef, useState, type ReactNode } from 'react'

export function ErrorMessage({ message }: { message: string | null | undefined }) {
  if (!message) return null
  return (
    <p className="error" role="alert">
      {message}
    </p>
  )
}

export function Notice({ children }: { children: ReactNode }) {
  return <p className="notice">{children}</p>
}

/** Bouton qui ouvre l'appareil photo arrière du téléphone (ou la galerie sur ordinateur). */
export function CameraButton({
  label,
  file,
  onChange,
}: {
  label: string
  file: File | null
  onChange: (file: File | null) => void
}) {
  const input = useRef<HTMLInputElement>(null)
  // Aperçu créé au moment du choix (pas dans un effet) ; ignoré si le parent a changé de fichier
  const [preview, setPreview] = useState<{ file: File; url: string } | null>(null)
  const shown = preview && preview.file === file ? preview.url : null

  function pick(next: File | null) {
    if (preview) URL.revokeObjectURL(preview.url)
    setPreview(next ? { file: next, url: URL.createObjectURL(next) } : null)
    onChange(next)
  }

  return (
    <div className="camera">
      <button type="button" className="camera-button" onClick={() => input.current?.click()}>
        {shown ? <img src={shown} alt={label} /> : <span className="camera-icon">📷</span>}
        <span>{file ? `${label} : reprendre` : label}</span>
      </button>
      {file && (
        <button type="button" className="link small" onClick={() => pick(null)}>
          Retirer
        </button>
      )}
      <input
        ref={input}
        type="file"
        accept="image/*"
        capture="environment"
        hidden
        onChange={(e) => {
          const chosen = e.target.files?.[0]
          if (chosen) pick(chosen) // appareil photo fermé sans photo : on garde la précédente
          e.target.value = '' // permet de reprendre la même photo
        }}
      />
    </div>
  )
}

/** Bouton en deux temps (évite les boîtes de dialogue du navigateur) : « Supprimer » puis « Confirmer ». */
export function ConfirmButton({
  label,
  confirmLabel,
  onConfirm,
  className = 'btn danger',
}: {
  label: string
  confirmLabel: string
  onConfirm: () => void
  className?: string
}) {
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    if (!armed) return
    const timer = setTimeout(() => setArmed(false), 4000)
    return () => clearTimeout(timer)
  }, [armed])
  return (
    <button type="button" className={className} onClick={() => (armed ? onConfirm() : setArmed(true))}>
      {armed ? confirmLabel : label}
    </button>
  )
}
