import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import ReactMarkdown from 'react-markdown'
import { sendChat, type ChatImageAttachment, type ChatMessage } from '../api'

type ChatAgent = 'executor' | 'research'

export default function AgentChat({ username }: { username: string | undefined }) {
  const [chatHistories, setChatHistories] = useState<Record<ChatAgent, ChatMessage[]>>({ executor: [], research: [] })
  const [chatInput, setChatInput] = useState('')
  const [chatAgent, setChatAgent] = useState<ChatAgent>('research')
  const [chatBusy, setChatBusy] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)
  const [attachedImages, setAttachedImages] = useState<Array<{ preview: string; attachment: ChatImageAttachment }>>([])
  const chatEndRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const chatMessages = chatHistories[chatAgent]

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chatMessages])

  const switchAgent = (agent: ChatAgent) => {
    setChatAgent(agent)
    setChatError(null)
  }

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    e.target.value = ''
    files.forEach(file => {
      const reader = new FileReader()
      reader.onload = ev => {
        const dataUrl = ev.target?.result as string
        const [header, data] = dataUrl.split(',')
        const media_type = header.match(/:(.*?);/)?.[1] ?? 'image/jpeg'
        setAttachedImages(prev => [...prev, { preview: dataUrl, attachment: { media_type, data } }])
      }
      reader.readAsDataURL(file)
    })
  }

  const sendMessage = async () => {
    const text = chatInput.trim()
    if ((!text && attachedImages.length === 0) || chatBusy) return

    const agent = chatAgent
    const prev = chatHistories[agent]
    const previews = attachedImages.map(a => a.preview)
    const images = attachedImages.map(a => a.attachment)
    const userMsg: ChatMessage = { role: 'user', content: text, imagePreviews: previews.length ? previews : undefined }
    const nextHistory = [...prev, userMsg]

    setChatHistories(h => ({ ...h, [agent]: nextHistory }))
    setChatInput('')
    setAttachedImages([])
    setChatBusy(true)
    setChatError(null)

    try {
      const { reply } = await sendChat(text, agent, prev, username ?? 'operator', images)
      setChatHistories(h => ({ ...h, [agent]: [...nextHistory, { role: 'assistant', content: reply }] }))
    } catch (e) {
      setChatError(String(e))
    }

    setChatBusy(false)
  }

  return (
    <div className="card home-chat-card">
      <div className="home-card-header" style={{ marginBottom: '0.75rem' }}>
        <h2 style={{ margin: 0 }}>Agent Chat</h2>
        <div style={{ display: 'flex', gap: '0.375rem' }}>
          <button
            className={`btn home-agent-btn ${chatAgent === 'research' ? 'home-agent-btn-active' : ''}`}
            onClick={() => switchAgent('research')}
          >
            Research
          </button>
          <button
            className={`btn home-agent-btn ${chatAgent === 'executor' ? 'home-agent-btn-active' : ''}`}
            onClick={() => switchAgent('executor')}
          >
            Executor
          </button>
        </div>
      </div>

      <div className="chat-messages home-chat-messages">
        {chatMessages.length === 0 && (
          <div className="home-chat-empty">
            <span style={{ fontSize: '1.5rem', opacity: 0.3 }}>
              {chatAgent === 'research' ? '🔬' : '⚡'}
            </span>
            <p className="muted" style={{ fontSize: '0.8125rem', fontStyle: 'italic', marginTop: '0.5rem' }}>
              Ask the {chatAgent} agent anything…
            </p>
          </div>
        )}
        {chatMessages.map((m, i) => (
          <div key={i} className={`chat-bubble chat-bubble-${m.role}`}>
            <span className="chat-role">
              {m.role === 'user' ? (username ?? 'you') : chatAgent === 'executor' ? 'Executor' : 'Research'}
            </span>
            {m.imagePreviews && m.imagePreviews.length > 0 && (
              <div className="chat-image-previews">
                {m.imagePreviews.map((src, j) => (
                  <img key={j} src={src} className="chat-bubble-image" alt="attached" />
                ))}
              </div>
            )}
            {m.role === 'assistant' ? (
              <div className="chat-content chat-md">
                <ReactMarkdown>{m.content}</ReactMarkdown>
              </div>
            ) : (
              m.content ? <span className="chat-content">{m.content}</span> : null
            )}
          </div>
        ))}
        {chatBusy && (
          <div className="chat-bubble chat-bubble-assistant">
            <span className="chat-role">{chatAgent === 'executor' ? 'Executor' : 'Research'}</span>
            <span className="chat-content home-thinking">
              <span className="home-thinking-dot" /><span className="home-thinking-dot" /><span className="home-thinking-dot" />
            </span>
          </div>
        )}
        <div ref={chatEndRef} />
      </div>

      {chatError && <p className="error" style={{ marginTop: '0.5rem', fontSize: '0.8125rem' }}>{chatError}</p>}

      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        multiple
        style={{ display: 'none' }}
        onChange={handleFileChange}
      />
      {attachedImages.length > 0 && (
        <div className="chat-attach-preview-row">
          {attachedImages.map((img, i) => (
            <div key={i} className="chat-attach-thumb-wrap">
              <img src={img.preview} className="chat-attach-thumb" alt="attachment" />
              <button
                className="chat-attach-remove"
                onClick={() => setAttachedImages(prev => prev.filter((_, j) => j !== i))}
                title="Remove"
              >x</button>
            </div>
          ))}
        </div>
      )}
      <div className="chat-input-row">
        <button
          className="btn btn-gray chat-attach-btn"
          onClick={() => fileInputRef.current?.click()}
          disabled={chatBusy}
          title="Attach image"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66L9.42 16.41a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
          </svg>
        </button>
        <input
          className="chat-input"
          type="text"
          placeholder={`Message ${chatAgent} agent…`}
          value={chatInput}
          onChange={e => setChatInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') sendMessage() }}
          disabled={chatBusy}
        />
        <button
          className="btn btn-brand"
          onClick={sendMessage}
          disabled={chatBusy || (!chatInput.trim() && attachedImages.length === 0)}
        >
          {chatBusy ? <span className="spinner" /> : 'Send →'}
        </button>
      </div>
    </div>
  )
}
