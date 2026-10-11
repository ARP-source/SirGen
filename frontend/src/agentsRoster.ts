// Roster for the /agents page. Add a card here; the page renders whatever is listed.
// Numbers on a card always come from live state (see AgentCard.tsx); this file only names things.

export type AgentKind = 'vision' | 'llm' | 'code' | 'report'

/** Key into state.agents. 'procedure' and 'safety' are not in the backend yet; the page shows STANDBY until they are. */
export type AgentStateKey = 'surgical' | 'anesthesia' | 'record' | 'procedure' | 'safety'

export interface AgentCardConfig {
  id: string
  title: string
  subtitle: string
  category: string
  model: string
  task: string
  kind: AgentKind
  stateKey?: AgentStateKey
  /** Extra provenance shown in the detail panel only (keeps the card line short). */
  note?: string
}

export const LIGHTNING = 'Lightning (NVIDIA Nemotron) via vLLM · local, port 8000'

export const VISION_MODELS =
  'YOLO26n instrument detector (Cholec80-Boxes) · YOLO26m anatomy detector (Endoscapes2023) · ConvNeXt-Tiny CVS classifier (Endoscapes2023)'

export const AGENT_ROSTER: AgentCardConfig[] = [
  {
    id: 'vision',
    title: 'The Scrub Tech',
    subtitle: 'Vision agent',
    category: 'Computer vision',
    model: VISION_MODELS,
    task: 'Instrument, anatomy and Critical View of Safety detection, live from the video',
    kind: 'vision',
    note: 'Step tracking: rules over detected instruments (code, not a model)',
  },
  {
    id: 'procedure',
    title: 'The Attending',
    subtitle: 'Procedure agent',
    category: 'Reasoning · Lightning via vLLM',
    model: LIGHTNING,
    task: 'Reads the detected scene against the laparoscopic cholecystectomy steps: what is being done, what comes next, deviations',
    kind: 'llm',
    stateKey: 'procedure',
  },
  {
    id: 'surgical',
    title: 'The First Assistant',
    subtitle: 'Surgical agent',
    category: 'Reasoning · Lightning via vLLM',
    model: LIGHTNING,
    task: 'Reads the field: step, instruments in view, video events; speaks only through the gate',
    kind: 'llm',
    stateKey: 'surgical',
  },
  {
    id: 'anesthesia',
    title: 'The Anesthesiologist',
    subtitle: 'Anesthesia agent',
    category: 'Reasoning · Lightning via vLLM',
    model: LIGHTNING,
    task: 'Vitals trend, cuff readings and the drug log',
    kind: 'llm',
    stateKey: 'anesthesia',
  },
  {
    id: 'record',
    title: 'The Chart Reader',
    subtitle: 'Record agent',
    category: 'Reasoning · Lightning via vLLM',
    model: LIGHTNING,
    task: 'Patient chart: history, medications, allergies, labs',
    kind: 'llm',
    stateKey: 'record',
  },
  {
    id: 'safety',
    title: 'The Safety Officer',
    subtitle: 'Safety agent',
    category: 'Reasoning · Lightning via vLLM',
    model: LIGHTNING,
    task: 'Checks every proposed message against the procedure and the record before it is spoken',
    kind: 'llm',
    stateKey: 'safety',
  },
  {
    id: 'gate',
    title: 'The Gatekeeper',
    subtitle: 'Speak-or-hold gate',
    category: 'Code, not a model',
    model: 'Threshold rules on the instrument in use (code, not a model)',
    task: 'Holds speech while a clipper, scissors, hook or bipolar is in use; only an emergency breaks through',
    kind: 'code',
  },
  {
    id: 'archivist',
    title: 'The Archivist',
    subtitle: 'Reports',
    category: 'Code + Lightning summary',
    model: 'SHA-256 hash chain (code) · narrative draft by Lightning (NVIDIA Nemotron) via vLLM',
    task: 'Tamper-evident audit log, surgery log and post-op report draft',
    kind: 'report',
  },
]
