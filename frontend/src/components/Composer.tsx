import React, { useEffect, useRef, useState } from 'react'

/**
 * Props accepted by the Composer component.
 */
type Props = {
  onSend: (text: string) => Promise<void> | void
  disabled?: boolean
}

/**
 * Controlled textarea for authoring a new question.
 */
export default function Composer({ onSend, disabled }: Props) {
  const [text, setText] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${textarea.scrollHeight}px`
  }, [text])

  const submit = async () => {
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    await onSend(trimmed)
    setText('')
    textareaRef.current?.focus()
  }

  const onKeyDown: React.KeyboardEventHandler<HTMLTextAreaElement> = (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void submit()
    }
  }

  return (
    <div className="composer">
      <textarea
        ref={textareaRef}
        className="composer-input"
        placeholder="Type your question about the service desk workflow…"
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
        rows={1}
        aria-label="Message input"
        disabled={disabled}
      />
      <button className="composer-send" type="button" onClick={submit} disabled={disabled} aria-label="Ask">
        Ask
      </button>
    </div>
  )
}
