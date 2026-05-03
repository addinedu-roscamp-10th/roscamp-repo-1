import { useState, useEffect, useRef, useCallback } from 'react'

// ── constants ────────────────────────────────────────────────────────────────

const PINKY_ROBOT_IDS = ['sshopy1', 'sshopy2', 'sshopy3']

const COLORS = {
  green:  '#16a34a',
  red:    '#dc2626',
  gray:   '#6b7280',
  blue:   '#2563eb',
  purple: '#7c3aed',
  orange: '#ea580c',
}

// ── WebSocket hook ────────────────────────────────────────────────────────────

function useFleet() {
  const [robots, setRobots] = useState([])
  const [wsState, setWsState] = useState('disconnected')
  const wsRef = useRef(null)

  useEffect(() => {
    function connect() {
      const ws = new WebSocket(`ws://${location.host}/ws/robots`)
      wsRef.current = ws
      ws.onopen  = () => setWsState('connected')
      ws.onclose = () => { setWsState('disconnected'); setTimeout(connect, 2000) }
      ws.onerror = () => setWsState('error')
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data)
        if (msg.type === 'fleet_status') setRobots(msg.data)
      }
    }
    connect()
    return () => wsRef.current?.close()
  }, [])

  return { robots, wsState }
}

// ── API helpers ───────────────────────────────────────────────────────────────

async function postCmdVel(robotId, linear_x, angular_z) {
  const res = await fetch(`/robots/${robotId}/cmd_vel`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ linear_x, angular_z }),
  })
  return res.json()
}

async function postTriggerWork(robotId, sshopy_id) {
  const res = await fetch(`/robots/${robotId}/trigger_work`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sshopy_id }),
  })
  return res.json()
}

async function postGoalPose(robotId, x, y, theta = 0.0) {
  const res = await fetch(`/robots/${robotId}/goal_pose`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ x, y, theta }),
  })
  return res.json()
}

// ── shared UI components ──────────────────────────────────────────────────────

function StatusDot({ on }) {
  return (
    <span style={{
      display: 'inline-block', width: 10, height: 10, borderRadius: '50%',
      background: on ? COLORS.green : COLORS.red, marginRight: 6,
    }} />
  )
}

function SmallBtn({ label, color = COLORS.blue, onClick }) {
  const [fb, setFb] = useState('')
  async function handle() {
    setFb('...')
    try {
      const r = await onClick()
      setFb(r?.ok ? '✓' : '✗')
    } catch { setFb('✗') }
    setTimeout(() => setFb(''), 1200)
  }
  return (
    <button onClick={handle} style={{
      background: color, color: '#fff', border: 'none',
      borderRadius: 6, padding: '8px 14px', fontSize: 14,
      cursor: 'pointer', minWidth: 60,
    }}>
      {label}{fb ? ` ${fb}` : ''}
    </button>
  )
}

// ── 시나리오 stage 레이블 ─────────────────────────────────────────────────────

const INBOUND_STAGE_LABELS = {
  30: '입고위치 이동', 31: 'FrontJet 상차', 32: '창고 이동',
  33: '스캔 대기', 34: 'WareJet 적재', 35: '홈 복귀',
}

const RETRIEVAL_STAGE_LABELS = {
  20: '입구 이동', 21: 'FrontJet 상차', 22: '상품 식별 대기',
  23: '창고 이동', 24: 'WareJet 적재', 25: 'DB복구 대기', 26: '홈 복귀',
}

// ── Preset positions (map frame, 2026-04-24 기준) ────────────────────────────
// quaternion (oz, ow) → theta(yaw)
const qToTheta = (oz, ow) => 2 * Math.atan2(oz, ow)

const LOCATIONS = [
  { key: 'home',      label: '🏠 홈',       x: 0.905, y: -0.006, oz:  0.230, ow: 0.973 },
  { key: 'frontjet',  label: '📥 입고/회수',  x: 0.615, y:  0.487, oz:  0.730, ow: 0.684 },
  { key: 'warejet',   label: '📦 창고',      x: 0.015, y:  0.246, oz:  0.003, ow: 1.000 },
  { key: 'charging',  label: '🔋 충전',      x: 0.278, y:  0.642, oz: -0.720, ow: 0.694 },
  { key: 'tryzone_1', label: '👕 시착 1',    x: 1.227, y:  0.105, oz:  0.731, ow: 0.682 },
  { key: 'tryzone_2', label: '👕 시착 2',    x: 1.547, y:  0.257, oz:  1.000, ow: 0.031 },
  { key: 'tryzone_3', label: '👕 시착 3',    x: 1.352, y:  0.563, oz: -0.744, ow: 0.668 },
  { key: 'tryzone_4', label: '👕 시착 4',    x: 1.034, y:  0.384, oz:  0.005, ow: 1.000 },
].map(l => ({ ...l, theta: qToTheta(l.oz, l.ow) }))

const LOC = Object.fromEntries(LOCATIONS.map(l => [l.key, l]))

// 배달 시나리오용 (key 호환 유지)
const HOME_POSE     = LOC.home
const WARE_JET_POSE = LOC.warejet
const FRONT_POSE    = LOC.frontjet

const DELIVERY_STAGES = [
  { ...WARE_JET_POSE, key: 'warehouse', label: '창고' },
  { ...FRONT_POSE,    key: 'store',     label: '매장' },
  { ...HOME_POSE,     key: 'home',      label: '홈'  },
]
const ARRIVAL_THRESH = 0.30  // metres

// ── Pinky card ────────────────────────────────────────────────────────────────

function PinkyCard({ robot, addLog }) {
  const {
    robot_id, connected, battery, pose,
    tryon_stage, tryon_seat,
    inbound_stage, inbound_task_id,
    retrieval_stage, retrieval_task_id,
  } = robot
  const [goalX, setGoalX] = useState(String(HOME_POSE.x))
  const [goalY, setGoalY] = useState(String(HOME_POSE.y))
  const [goalFb, setGoalFb] = useState('')
  const [deliveryIdx, setDeliveryIdx] = useState(null)
  const [tryonSeatSel, setTryonSeatSel] = useState(1)   // 시나리오2 좌석 선택
  const goalSentAtRef  = useRef(0)
  const armWorkingRef  = useRef(false)   // arm 작동 중 → 도착 감지 차단
  const cancelledRef   = useRef(false)   // 취소 여부

  // 시나리오 1 (입고) 입력 상태
  const [ibProductId, setIbProductId] = useState('NK-AF1')
  const [ibSize, setIbSize]           = useState('270')
  const [ibColor, setIbColor]         = useState('white')
  const [ibQty, setIbQty]             = useState('1')
  const [ibFb, setIbFb]               = useState('')
  const [ibScanResult, setIbScanResult] = useState('{"product_id":"NK-AF1","warehouse_pos":"A-1-3"}')

  // 시나리오 4 (회수) 입력 상태
  const [rtProductId, setRtProductId] = useState('NK-AF1')
  const [rtSize, setRtSize]           = useState('270')
  const [rtColor, setRtColor]         = useState('white')
  const [rtFb, setRtFb]               = useState('')

  // cancel delivery when robot disconnects
  useEffect(() => {
    if (!connected && deliveryIdx !== null) {
      cancelledRef.current = true
      setDeliveryIdx(null)
      addLog?.(`${robot_id} 연결 끊김 — 배달 취소`, 'warn')
    }
  }, [connected])

  // arrival detection — cooldown 5s + arm wait
  useEffect(() => {
    if (deliveryIdx === null || !pose || armWorkingRef.current) return
    if (Date.now() - goalSentAtRef.current < 5000) return
    const target = DELIVERY_STAGES[deliveryIdx]
    const dist = Math.hypot(pose.x - target.x, pose.y - target.y)
    if (dist < ARRIVAL_THRESH) {
      armWorkingRef.current = true
      const key = DELIVERY_STAGES[deliveryIdx].key
      const label = DELIVERY_STAGES[deliveryIdx].label
      const idx = deliveryIdx
      addLog?.(`${robot_id} → ${label} 도착`, 'ok')

      const advance = async () => {
        try {
          if (key === 'warehouse') {
            addLog?.('ware_jet 그리퍼 동작 중...', 'info')
            await fetch('/robots/ware_jet/arm_test', { method: 'POST' })
            addLog?.('ware_jet 그리퍼 완료', 'ok')
          } else if (key === 'store') {
            addLog?.('front_jet 그리퍼 동작 중...', 'info')
            await fetch('/robots/front_jet/arm_test', { method: 'POST' })
            addLog?.('front_jet 그리퍼 완료', 'ok')
          }
        } catch { addLog?.('arm 통신 오류', 'err') }

        armWorkingRef.current = false
        if (cancelledRef.current) return

        const next = idx + 1
        if (next >= DELIVERY_STAGES.length) {
          setDeliveryIdx(null)
          addLog?.(`${robot_id} 배달 완료`, 'ok')
        } else {
          const wp = DELIVERY_STAGES[next]
          postGoalPose(robot_id, wp.x, wp.y, wp.theta)
          goalSentAtRef.current = Date.now()
          setDeliveryIdx(next)
          addLog?.(`${robot_id} → ${DELIVERY_STAGES[next].label} 이동`, 'info')
        }
      }
      advance()
    }
  }, [pose, deliveryIdx])

  function startDelivery() {
    cancelledRef.current = false
    armWorkingRef.current = false
    const wp = DELIVERY_STAGES[0]
    postGoalPose(robot_id, wp.x, wp.y, wp.theta)
    goalSentAtRef.current = Date.now()
    setDeliveryIdx(0)
    addLog?.(`${robot_id} 배달 시작 → ${DELIVERY_STAGES[0].label}`, 'info')
  }

  function cancelDelivery() {
    cancelledRef.current = true
    armWorkingRef.current = false
    postCmdVel(robot_id, 0, 0)
    setDeliveryIdx(null)
    addLog?.(`${robot_id} 배달 취소`, 'warn')
  }

  const battColor = battery == null ? COLORS.gray
    : battery < 20 ? COLORS.red
    : battery < 50 ? COLORS.orange
    : COLORS.green

  async function sendGoal(x, y, theta = 0.0) {
    setGoalFb('...')
    try {
      const r = await postGoalPose(robot_id, x, y, theta)
      setGoalFb(r?.ok ? '✓ 이동 중' : '✗ 실패')
    } catch { setGoalFb('✗ 오류') }
    setTimeout(() => setGoalFb(''), 3000)
  }

  return (
    <div style={cardStyle(connected)}>
      <div style={cardHeader}>
        <StatusDot on={connected} />
        <b>{robot_id}</b>
        <span style={{ marginLeft: 'auto', fontSize: 12, color: COLORS.gray }}>Pinky</span>
      </div>

      <div style={infoRow}>
        <span>배터리</span>
        <b style={{ color: battColor }}>
          {battery != null ? `${battery.toFixed(1)}%` : '—'}
        </b>
      </div>
      <div style={infoRow}>
        <span>위치</span>
        <b style={{ fontSize: 12 }}>
          {pose ? `x ${pose.x.toFixed(2)}, y ${pose.y.toFixed(2)}` : '—'}
        </b>
      </div>

      {connected && (
        <>
          {/* 방향키 */}
          <div style={{ marginTop: 10 }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
              <SmallBtn label="▲" onClick={() => postCmdVel(robot_id, 0.2, 0)} />
              <div style={{ display: 'flex', gap: 6 }}>
                <SmallBtn label="◄" onClick={() => postCmdVel(robot_id, 0, 0.5)} />
                <SmallBtn label="■" color={COLORS.gray} onClick={() => postCmdVel(robot_id, 0, 0)} />
                <SmallBtn label="►" onClick={() => postCmdVel(robot_id, 0, -0.5)} />
              </div>
              <SmallBtn label="▼" color={COLORS.purple} onClick={() => postCmdVel(robot_id, -0.2, 0)} />
            </div>
          </div>

          {/* 홈 / 절대 좌표 이동 */}
          <div style={{ marginTop: 12, borderTop: '1px solid #e5e7eb', paddingTop: 10 }}>
            <div style={{ fontSize: 11, color: COLORS.gray, marginBottom: 6 }}>절대 좌표 이동 (map)</div>
            <div style={{ display: 'flex', gap: 4, marginBottom: 6 }}>
              <input
                type="number" step="0.1" value={goalX}
                onChange={e => setGoalX(e.target.value)}
                placeholder="x"
                style={inputStyle}
              />
              <input
                type="number" step="0.1" value={goalY}
                onChange={e => setGoalY(e.target.value)}
                placeholder="y"
                style={inputStyle}
              />
              <button
                onClick={() => sendGoal(parseFloat(goalX), parseFloat(goalY))}
                style={{ ...btnStyle, background: COLORS.blue, flex: 1 }}
              >
                이동
              </button>
            </div>
            <div style={{ display: 'flex', gap: 4, marginBottom: 4 }}>
              <select
                defaultValue=""
                onChange={e => {
                  const loc = LOC[e.target.value]
                  if (!loc) return
                  setGoalX(String(loc.x))
                  setGoalY(String(loc.y))
                  sendGoal(loc.x, loc.y, loc.theta)
                  e.target.value = ''  // reset so 같은 위치 재선택 가능
                }}
                style={{
                  ...inputStyle, flex: 2, cursor: 'pointer',
                  background: '#fff', minWidth: 0,
                }}
              >
                <option value="" disabled>📍 위치 선택…</option>
                {LOCATIONS.map(l => (
                  <option key={l.key} value={l.key}>{l.label}</option>
                ))}
              </select>
              <button
                onClick={() => {
                  // 1) Nav2 현재 goal 취소 — 현재 위치를 새 goal로 발행
                  if (pose) postGoalPose(robot_id, pose.x, pose.y, 0)
                  // 2) cmd_vel=0 으로 즉시 정지
                  postCmdVel(robot_id, 0, 0)
                  // 3) 진행 중인 배달 시나리오도 취소
                  cancelledRef.current = true
                  armWorkingRef.current = false
                  setDeliveryIdx(null)
                  addLog?.(`${robot_id} 이동 정지`, 'warn')
                  setGoalFb('🛑 정지')
                  setTimeout(() => setGoalFb(''), 2000)
                }}
                style={{ ...btnStyle, background: COLORS.red, flex: 1 }}
              >🛑 정지</button>
            </div>
            {goalFb && (
              <div style={{ marginTop: 4, fontSize: 12, textAlign: 'center', color: COLORS.gray }}>
                {goalFb}
              </div>
            )}
          </div>

          {/* 배달 태스크 */}
          <div style={{ marginTop: 12, borderTop: '1px solid #e5e7eb', paddingTop: 10 }}>
            <div style={{ fontSize: 11, color: COLORS.gray, marginBottom: 6 }}>배달 태스크</div>
            {deliveryIdx !== null ? (
              <>
                <div style={{ display: 'flex', justifyContent: 'center', gap: 4, marginBottom: 6, fontSize: 13 }}>
                  {DELIVERY_STAGES.map((s, i) => (
                    <span key={s.key} style={{
                      color: i < deliveryIdx ? COLORS.green
                           : i === deliveryIdx ? COLORS.orange
                           : COLORS.gray,
                      fontWeight: i === deliveryIdx ? 700 : 400,
                    }}>
                      {i > 0 && <span style={{ color: COLORS.gray }}> → </span>}
                      {s.label}
                    </span>
                  ))}
                </div>
                <div style={{ fontSize: 12, textAlign: 'center', color: COLORS.orange, marginBottom: 6 }}>
                  {DELIVERY_STAGES[deliveryIdx].label} 이동 중...
                </div>
                <button
                  onClick={cancelDelivery}
                  style={{ ...btnStyle, background: COLORS.red, width: '100%' }}
                >
                  배달 취소
                </button>
              </>
            ) : (
              <button
                onClick={startDelivery}
                style={{ ...btnStyle, background: COLORS.purple, width: '100%' }}
              >
                🚀 배달 시작 (창고 → 매장 → 홈)
              </button>
            )}
          </div>

          {/* 시나리오 2 (시착) — TC 2-06/14/16/17/19/21 */}
          <div style={{ marginTop: 12, borderTop: '1px solid #e5e7eb', paddingTop: 10 }}>
            <div style={{ fontSize: 11, color: COLORS.gray, marginBottom: 6 }}>
              시나리오 2 (시착) {tryon_stage != null && <span style={{ color: COLORS.orange }}>● 진행 중 (stage {tryon_stage}{tryon_seat ? `, seat ${tryon_seat}` : ''})</span>}
            </div>
            {tryon_stage == null ? (
              <div style={{ display: 'flex', gap: 4 }}>
                <select
                  value={tryonSeatSel}
                  onChange={e => setTryonSeatSel(parseInt(e.target.value))}
                  style={{ ...inputStyle, flex: 1, cursor: 'pointer', background: '#fff' }}
                >
                  {[1,2,3,4].map(n => <option key={n} value={n}>👕 좌석 {n}</option>)}
                </select>
                <button
                  onClick={async () => {
                    setGoalFb('...')
                    try {
                      const r = await fetch(`/tryon/start?robot_id=${robot_id}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                          seat_id: tryonSeatSel,
                          product_id: 'admin-ui-demo',
                          color: null, size: null,
                        }),
                      }).then(r => r.json())
                      if (r.ok) {
                        addLog?.(`${robot_id} 시착 시작 → 좌석 ${tryonSeatSel}`, 'info')
                        setGoalFb('✓ 시착 시작')
                      } else {
                        addLog?.(`${robot_id} 시착 실패: ${r.message}`, 'err')
                        setGoalFb(`✗ ${r.message}`)
                      }
                    } catch { setGoalFb('✗ 오류') }
                    setTimeout(() => setGoalFb(''), 3000)
                  }}
                  style={{ ...btnStyle, background: COLORS.blue, flex: 2 }}
                >🎫 시착 요청</button>
              </div>
            ) : (
              <div style={{ display: 'flex', gap: 4 }}>
                {tryon_stage === 12 /* AT_TRYZONE */ && (
                  <button
                    onClick={async () => {
                      try {
                        const r = await fetch(`/tryon/pickup_complete?robot_id=${robot_id}`, { method: 'POST' }).then(r => r.json())
                        addLog?.(r.ok ? `${robot_id} 수령 완료 → 회수존 이동` : `수령완료 실패: ${r.message}`, r.ok ? 'ok' : 'err')
                      } catch { addLog?.('수령완료 오류', 'err') }
                    }}
                    style={{ ...btnStyle, background: COLORS.green, flex: 1 }}
                  >📦 수령 완료</button>
                )}
                <button
                  onClick={async () => {
                    try {
                      await fetch(`/tryon/cancel?robot_id=${robot_id}`, { method: 'POST' })
                      addLog?.(`${robot_id} 시착 시나리오 중단`, 'warn')
                    } catch { addLog?.('시착 중단 오류', 'err') }
                  }}
                  style={{ ...btnStyle, background: COLORS.red, flex: 1 }}
                >🛑 시착 중단</button>
              </div>
            )}
          </div>

          {/* 시나리오 1 (입고) */}
          <div style={{ marginTop: 12, borderTop: '1px solid #e5e7eb', paddingTop: 10 }}>
            <div style={{ fontSize: 11, color: COLORS.gray, marginBottom: 6 }}>
              시나리오 1 (입고){' '}
              {inbound_stage != null && (
                <span style={{ color: COLORS.blue }}>
                  ● {INBOUND_STAGE_LABELS[inbound_stage] ?? `stage ${inbound_stage}`}
                </span>
              )}
            </div>
            {inbound_stage == null ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <div style={{ display: 'flex', gap: 4 }}>
                  <input
                    value={ibProductId} onChange={e => setIbProductId(e.target.value)}
                    placeholder="상품ID" style={{ ...inputStyle, flex: 2 }}
                  />
                  <input
                    value={ibSize} onChange={e => setIbSize(e.target.value)}
                    placeholder="사이즈" style={{ ...inputStyle, flex: 1 }}
                  />
                </div>
                <div style={{ display: 'flex', gap: 4 }}>
                  <input
                    value={ibColor} onChange={e => setIbColor(e.target.value)}
                    placeholder="색상" style={{ ...inputStyle, flex: 2 }}
                  />
                  <input
                    value={ibQty} onChange={e => setIbQty(e.target.value)}
                    placeholder="수량" style={{ ...inputStyle, flex: 1 }}
                  />
                </div>
                <button
                  onClick={async () => {
                    setIbFb('...')
                    try {
                      const r = await fetch('/inbound/start', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                          robot_id,
                          items: [{ product_id: ibProductId, size: parseInt(ibSize) || 270, color: ibColor, quantity: parseInt(ibQty) || 1 }],
                        }),
                      }).then(r => r.json())
                      if (r.ok) {
                        addLog?.(`${robot_id} 입고 시작 (task: ${r.task_id})`, 'info')
                        setIbFb('✓')
                      } else {
                        addLog?.(`${robot_id} 입고 실패: ${r.message}`, 'err')
                        setIbFb(`✗ ${r.message}`)
                      }
                    } catch { setIbFb('✗ 오류') }
                    setTimeout(() => setIbFb(''), 3000)
                  }}
                  style={{ ...btnStyle, background: COLORS.blue, width: '100%' }}
                >📦 입고 시작{ibFb ? ` ${ibFb}` : ''}</button>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {inbound_stage === 33 /* SCAN_WAIT */ && (
                  <>
                    <div style={{ fontSize: 11, color: COLORS.gray, marginBottom: 2 }}>스캔 결과 JSON</div>
                    <input
                      value={ibScanResult} onChange={e => setIbScanResult(e.target.value)}
                      style={{ ...inputStyle, width: '100%', fontFamily: 'monospace', fontSize: 11 }}
                    />
                    <button
                      onClick={async () => {
                        setIbFb('...')
                        try {
                          let scan = {}
                          try { scan = JSON.parse(ibScanResult) } catch { scan = { product_id: ibProductId, warehouse_pos: 'A-1-3' } }
                          const r = await fetch('/inbound/scan_complete', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ task_id: inbound_task_id, scan_result: scan }),
                          }).then(r => r.json())
                          addLog?.(r.ok ? `${robot_id} 스캔 완료 → WareJet 적재` : `스캔완료 실패: ${r.message}`, r.ok ? 'ok' : 'err')
                          setIbFb(r.ok ? '✓' : `✗ ${r.message}`)
                        } catch { setIbFb('✗ 오류') }
                        setTimeout(() => setIbFb(''), 3000)
                      }}
                      style={{ ...btnStyle, background: COLORS.green, width: '100%' }}
                    >🔍 스캔 완료 통보{ibFb ? ` ${ibFb}` : ''}</button>
                  </>
                )}
                <button
                  onClick={async () => {
                    try {
                      const r = await fetch(`/inbound/cancel?task_id=${inbound_task_id}`, { method: 'POST' }).then(r => r.json())
                      addLog?.(r.ok ? `${robot_id} 입고 취소` : `입고취소 실패: ${r.message}`, r.ok ? 'warn' : 'err')
                    } catch { addLog?.('입고취소 오류', 'err') }
                  }}
                  style={{ ...btnStyle, background: COLORS.red, width: '100%' }}
                >🛑 입고 취소</button>
              </div>
            )}
          </div>

          {/* 시나리오 4 (회수) */}
          <div style={{ marginTop: 12, borderTop: '1px solid #e5e7eb', paddingTop: 10 }}>
            <div style={{ fontSize: 11, color: COLORS.gray, marginBottom: 6 }}>
              시나리오 4 (회수){' '}
              {retrieval_stage != null && (
                <span style={{ color: COLORS.purple }}>
                  ● {RETRIEVAL_STAGE_LABELS[retrieval_stage] ?? `stage ${retrieval_stage}`}
                </span>
              )}
            </div>
            {retrieval_stage == null ? (
              <button
                onClick={async () => {
                  setRtFb('...')
                  try {
                    const r = await fetch(`/retrieval/start?robot_id=${robot_id}`, { method: 'POST' }).then(r => r.json())
                    if (r.ok) {
                      addLog?.(`${robot_id} 회수 시작 (task: ${r.task_id})`, 'info')
                      setRtFb('✓')
                    } else {
                      addLog?.(`${robot_id} 회수 실패: ${r.message}`, 'err')
                      setRtFb(`✗ ${r.message}`)
                    }
                  } catch { setRtFb('✗ 오류') }
                  setTimeout(() => setRtFb(''), 3000)
                }}
                style={{ ...btnStyle, background: COLORS.purple, width: '100%' }}
              >🔄 회수 시작{rtFb ? ` ${rtFb}` : ''}</button>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {retrieval_stage === 22 /* IDENTIFY */ && (
                  <>
                    <div style={{ display: 'flex', gap: 4 }}>
                      <input
                        value={rtProductId} onChange={e => setRtProductId(e.target.value)}
                        placeholder="상품ID" style={{ ...inputStyle, flex: 2 }}
                      />
                      <input
                        value={rtSize} onChange={e => setRtSize(e.target.value)}
                        placeholder="사이즈" style={{ ...inputStyle, flex: 1 }}
                      />
                    </div>
                    <input
                      value={rtColor} onChange={e => setRtColor(e.target.value)}
                      placeholder="색상" style={{ ...inputStyle }}
                    />
                    <button
                      onClick={async () => {
                        setRtFb('...')
                        try {
                          const r = await fetch('/retrieval/identify', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                              task_id: retrieval_task_id,
                              product_id: rtProductId,
                              size: parseInt(rtSize) || null,
                              color: rtColor || null,
                              quantity: 1,
                            }),
                          }).then(r => r.json())
                          addLog?.(r.ok ? `${robot_id} 상품 식별 → 창고 이동` : `식별 실패: ${r.message}`, r.ok ? 'ok' : 'err')
                          setRtFb(r.ok ? '✓' : `✗ ${r.message}`)
                        } catch { setRtFb('✗ 오류') }
                        setTimeout(() => setRtFb(''), 3000)
                      }}
                      style={{ ...btnStyle, background: COLORS.green, width: '100%' }}
                    >🔎 상품 식별 완료{rtFb ? ` ${rtFb}` : ''}</button>
                  </>
                )}
                {retrieval_stage === 25 /* DB_RESTORE */ && (
                  <button
                    onClick={async () => {
                      setRtFb('...')
                      try {
                        const r = await fetch('/retrieval/db_restored', {
                          method: 'POST',
                          headers: { 'Content-Type': 'application/json' },
                          body: JSON.stringify({ task_id: retrieval_task_id }),
                        }).then(r => r.json())
                        addLog?.(r.ok ? `${robot_id} DB 복구 완료 → 홈 복귀` : `DB복구 실패: ${r.message}`, r.ok ? 'ok' : 'err')
                        setRtFb(r.ok ? '✓' : `✗ ${r.message}`)
                      } catch { setRtFb('✗ 오류') }
                      setTimeout(() => setRtFb(''), 3000)
                    }}
                    style={{ ...btnStyle, background: COLORS.green, width: '100%' }}
                  >🗄 DB 복구 완료{rtFb ? ` ${rtFb}` : ''}</button>
                )}
                <button
                  onClick={async () => {
                    try {
                      const r = await fetch(`/retrieval/cancel?task_id=${retrieval_task_id}`, { method: 'POST' }).then(r => r.json())
                      addLog?.(r.ok ? `${robot_id} 회수 취소` : `회수취소 실패: ${r.message}`, r.ok ? 'warn' : 'err')
                    } catch { addLog?.('회수취소 오류', 'err') }
                  }}
                  style={{ ...btnStyle, background: COLORS.red, width: '100%' }}
                >🛑 회수 취소</button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

// ── WareJet panel (camera + teaching) ────────────────────────────────────────

const ARM_URL = '/arm-server'  // vite proxy → http://192.168.1.115:8001
const JOINT_LIMIT = 130

function WareJetPanel() {
  const [angles, setAngles] = useState([0, 0, 0, 0, 0, 0])
  const [speed, setSpeed] = useState(15)
  const [fb, setFb] = useState('')

  function setJoint(i, val) {
    const v = Math.max(-JOINT_LIMIT, Math.min(JOINT_LIMIT, Number(val)))
    setAngles(prev => prev.map((a, idx) => idx === i ? v : a))
  }

  async function readJoints() {
    try {
      const r = await fetch(`${ARM_URL}/arm/joints`)
      const d = await r.json()
      setAngles(d.angles.map(a => Math.round(a * 10) / 10))
      setFb('읽기 완료')
    } catch { setFb('읽기 실패') }
    setTimeout(() => setFb(''), 2000)
  }

  async function sendJoints() {
    setFb('전송 중...')
    try {
      const r = await fetch(`${ARM_URL}/arm/angles`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ angles, speed }),
      })
      const d = await r.json()
      setFb(d.ok ? '전송 완료' : '실패')
    } catch { setFb('오류') }
    setTimeout(() => setFb(''), 2000)
  }

  return (
    <div style={{ marginTop: 10, borderTop: '1px solid #e5e7eb', paddingTop: 10 }}>
      {/* 카메라 */}
      <div style={{ fontSize: 11, color: COLORS.gray, marginBottom: 6 }}>카메라</div>
      <img
        src={`${ARM_URL}/stream`}
        alt="ware_jet cam"
        style={{ width: '100%', borderRadius: 6, background: '#000', marginBottom: 6 }}
        onError={e => { e.target.style.display = 'none' }}
      />
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
        <SmallBtn label="📐 범위 테스트" color={COLORS.blue}
          onClick={() => fetch(`${ARM_URL}/arm/range_test`, { method: 'POST' }).then(r => r.json())} />
        <SmallBtn label="🎯 자동 하강" color={COLORS.purple}
          onClick={() => fetch(`${ARM_URL}/arm/auto_lower`, { method: 'POST' }).then(r => r.json())} />
        <SmallBtn label="⏹ 정지" color={COLORS.red}
          onClick={() => fetch(`${ARM_URL}/arm/stop`, { method: 'POST' }).then(r => r.json())} />
        <SmallBtn label="🏠 리셋" color={COLORS.gray}
          onClick={() => fetch(`${ARM_URL}/arm/reset`, { method: 'POST' }).then(r => r.json())} />
      </div>

      {/* 티칭 */}
      <div style={{ fontSize: 11, color: COLORS.gray, marginBottom: 8, borderTop: '1px solid #e5e7eb', paddingTop: 8 }}>
        티칭 (−{JOINT_LIMIT} ~ +{JOINT_LIMIT}°)
      </div>
      {angles.map((val, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
          <span style={{ fontSize: 12, width: 44, flexShrink: 0, color: COLORS.gray }}>J{i + 1}</span>
          <input
            type="range" min={-JOINT_LIMIT} max={JOINT_LIMIT} step={1}
            value={val}
            onChange={e => setJoint(i, e.target.value)}
            style={{ flex: 1 }}
          />
          <input
            type="number" min={-JOINT_LIMIT} max={JOINT_LIMIT} step={1}
            value={val}
            onChange={e => setJoint(i, e.target.value)}
            style={{ width: 52, padding: '3px 4px', borderRadius: 4, border: '1px solid #d1d5db', fontSize: 12, textAlign: 'center' }}
          />
        </div>
      ))}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
        <span style={{ fontSize: 12, width: 44, flexShrink: 0, color: COLORS.gray }}>속도</span>
        <input
          type="range" min={5} max={50} step={5} value={speed}
          onChange={e => setSpeed(Number(e.target.value))}
          style={{ flex: 1 }}
        />
        <span style={{ width: 52, fontSize: 12, textAlign: 'center', color: COLORS.gray }}>{speed}</span>
      </div>
      <div style={{ display: 'flex', gap: 6 }}>
        <button onClick={readJoints}
          style={{ ...btnStyle, background: COLORS.gray, flex: 1 }}>
          📖 현재 위치 읽기
        </button>
        <button onClick={sendJoints}
          style={{ ...btnStyle, background: COLORS.orange, flex: 1 }}>
          ▶ 전송
        </button>
      </div>
      {fb && <div style={{ marginTop: 6, fontSize: 12, textAlign: 'center', color: COLORS.gray }}>{fb}</div>}
    </div>
  )
}

// ── Jetcobot card ─────────────────────────────────────────────────────────────

function JetcobotCard({ robot, pinkyIds }) {
  const { robot_id, connected, busy, joint_states, last_work_complete } = robot
  const [selectedPinky, setSelectedPinky] = useState(pinkyIds[0] ?? '')

  return (
    <div style={cardStyle(connected)}>
      <div style={cardHeader}>
        <StatusDot on={connected} />
        <b>{robot_id}</b>
        <span style={{ marginLeft: 'auto', fontSize: 12, color: COLORS.gray }}>Jetcobot</span>
      </div>

      <div style={infoRow}>
        <span>상태</span>
        <b style={{ color: busy ? COLORS.orange : COLORS.green }}>
          {busy ? '작업 중' : '대기'}
        </b>
      </div>
      {last_work_complete && (
        <div style={infoRow}>
          <span>완료</span>
          <b style={{ fontSize: 12 }}>{last_work_complete}</b>
        </div>
      )}

      {joint_states && (
        <div style={{ marginTop: 6, fontSize: 11, color: COLORS.gray }}>
          <div>joints: {joint_states.positions.slice(0, 6).map(p => p.toFixed(2)).join(', ')}</div>
        </div>
      )}

      {connected && (
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <select
            value={selectedPinky}
            onChange={e => setSelectedPinky(e.target.value)}
            style={{ padding: '6px 8px', borderRadius: 6, border: '1px solid #d1d5db', fontSize: 13 }}
          >
            {pinkyIds.map(id => <option key={id} value={id}>{id}</option>)}
          </select>
          <SmallBtn
            label={busy ? '작업 중...' : '▶ trigger_work'}
            color={busy ? COLORS.gray : COLORS.orange}
            onClick={() => postTriggerWork(robot_id, selectedPinky)}
          />
          <SmallBtn
            label="⬜ 초기위치 (0,0,0,0,0,0)"
            color={COLORS.gray}
            onClick={() => fetch(`/robots/${robot_id}/arm_reset`, { method: 'POST' }).then(r => r.json())}
          />
        </div>
      )}

      {/* ware_jet 전용: 카메라 스트림 + 범위 테스트 + 티칭 */}
      {robot_id === 'ware_jet' && connected && (
        <WareJetPanel />
      )}
    </div>
  )
}

// ── styles ────────────────────────────────────────────────────────────────────

function cardStyle(connected) {
  return {
    background: connected ? '#fff' : '#f8f8f8',
    border: `1px solid ${connected ? '#d1d5db' : '#e5e7eb'}`,
    borderRadius: 12,
    padding: 14,
    opacity: connected ? 1 : 0.7,
  }
}

const cardHeader = {
  display: 'flex', alignItems: 'center', marginBottom: 10, fontWeight: 600,
}

const infoRow = {
  display: 'flex', justifyContent: 'space-between',
  fontSize: 13, marginBottom: 4, padding: '2px 0',
}

const inputStyle = {
  flex: 1, padding: '6px 6px', borderRadius: 6,
  border: '1px solid #d1d5db', fontSize: 13, width: 0,
}

const btnStyle = {
  color: '#fff', border: 'none', borderRadius: 6,
  padding: '7px 10px', fontSize: 13, cursor: 'pointer',
}

// ── Clock hook ────────────────────────────────────────────────────────────────

function useClock() {
  const fmt = () => new Date().toLocaleTimeString('ko-KR', { hour12: false })
  const [time, setTime] = useState(fmt)
  useEffect(() => {
    const id = setInterval(() => setTime(fmt()), 1000)
    return () => clearInterval(id)
  }, [])
  return time
}

// ── Log hook ──────────────────────────────────────────────────────────────────

function useLog(max = 60) {
  const [logs, setLogs] = useState([])
  const addLog = useCallback((msg, level = 'info') => {
    const ts = new Date().toLocaleTimeString('ko-KR', { hour12: false })
    setLogs(prev => [{ ts, msg, level }, ...prev].slice(0, max))
  }, [])
  return [logs, addLog]
}

// ── LogPanel component ────────────────────────────────────────────────────────

function LogPanel({ logs }) {
  const levelColor = { info: '#374151', ok: COLORS.green, warn: COLORS.orange, err: COLORS.red }
  return (
    <div style={{ marginTop: 16, border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'hidden' }}>
      <div style={{ padding: '6px 12px', background: '#f9fafb', fontSize: 11, fontWeight: 700,
                    color: COLORS.gray, borderBottom: '1px solid #e5e7eb', textTransform: 'uppercase', letterSpacing: 1 }}>
        Log
      </div>
      <div style={{ maxHeight: 180, overflowY: 'auto', padding: '4px 0', fontFamily: 'monospace', fontSize: 11 }}>
        {logs.length === 0
          ? <div style={{ padding: '6px 12px', color: COLORS.gray }}>— 이벤트 없음 —</div>
          : logs.map((l, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, padding: '2px 12px',
                                   borderBottom: '1px solid #f3f4f6' }}>
              <span style={{ color: COLORS.gray, flexShrink: 0 }}>{l.ts}</span>
              <span style={{ color: levelColor[l.level] ?? '#374151' }}>{l.msg}</span>
            </div>
          ))
        }
      </div>
    </div>
  )
}

// ── MapView ───────────────────────────────────────────────────────────────────

const MAP_RES    = 0.020
const MAP_ORIGIN = [-0.203, -0.209]
const MAP_W      = 102
const MAP_H      = 53
const SCALE      = 4   // px per map pixel → 408×212 canvas

const WAYPOINTS = [
  { label: '홈',  color: COLORS.green,  ...HOME_POSE },
  { label: '창고', color: COLORS.orange, ...WARE_JET_POSE },
  { label: '매장', color: COLORS.blue,   ...FRONT_POSE },
]

function worldToCanvas(wx, wy) {
  const cx = (wx - MAP_ORIGIN[0]) / MAP_RES * SCALE
  const cy = (MAP_H - (wy - MAP_ORIGIN[1]) / MAP_RES) * SCALE
  return [cx, cy]
}

function MapView({ robots }) {
  const canvasRef = useRef(null)
  const [mapImg, setMapImg] = useState(null)

  useEffect(() => {
    const img = new Image()
    img.src = '/map/image'
    img.onload = () => setMapImg(img)
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !mapImg) return
    const ctx = canvas.getContext('2d')
    ctx.clearRect(0, 0, canvas.width, canvas.height)

    // map background (nearest-neighbor for crisp pixels)
    ctx.imageSmoothingEnabled = false
    ctx.drawImage(mapImg, 0, 0, canvas.width, canvas.height)

    // waypoint markers
    WAYPOINTS.forEach(({ label, color, x, y }) => {
      const [cx, cy] = worldToCanvas(x, y)
      ctx.beginPath()
      ctx.arc(cx, cy, 5, 0, Math.PI * 2)
      ctx.fillStyle = color + '55'
      ctx.strokeStyle = color
      ctx.lineWidth = 1.5
      ctx.fill(); ctx.stroke()
      ctx.fillStyle = color
      ctx.font = 'bold 9px sans-serif'
      ctx.fillText(label, cx + 6, cy + 4)
    })

    // robots
    const pinkyList = robots.filter(r => r.type === 'pinky' && r.pose)
    const robotColors = ['#7c3aed', '#db2777', '#0891b2']
    pinkyList.forEach((r, i) => {
      const [cx, cy] = worldToCanvas(r.pose.x, r.pose.y)
      const col = robotColors[i % robotColors.length]
      ctx.beginPath()
      ctx.arc(cx, cy, 6, 0, Math.PI * 2)
      ctx.fillStyle = col
      ctx.fill()
      ctx.fillStyle = '#fff'
      ctx.font = 'bold 8px sans-serif'
      ctx.textAlign = 'center'
      ctx.fillText(r.robot_id.replace('sshopy', 'P'), cx, cy + 3)
      ctx.textAlign = 'left'
    })
  }, [mapImg, robots])

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ marginBottom: 8, fontSize: 12, fontWeight: 700, color: COLORS.gray, textTransform: 'uppercase', letterSpacing: 1 }}>
        Map
      </div>
      <div style={{ border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'hidden', background: '#f9fafb' }}>
        <canvas
          ref={canvasRef}
          width={MAP_W * SCALE}
          height={MAP_H * SCALE}
          style={{ display: 'block', width: '100%' }}
        />
      </div>
      <div style={{ display: 'flex', gap: 10, marginTop: 6, flexWrap: 'wrap' }}>
        {WAYPOINTS.map(w => (
          <span key={w.label} style={{ fontSize: 11, color: w.color, display: 'flex', alignItems: 'center', gap: 3 }}>
            <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: w.color }} />
            {w.label}
          </span>
        ))}
        <span style={{ fontSize: 11, color: COLORS.gray }}>● Pinky 로봇</span>
      </div>
    </div>
  )
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const { robots, wsState } = useFleet()
  const clock = useClock()
  const [logs, addLog] = useLog()

  const pinkyRobots    = robots.filter(r => r.type === 'pinky')
  const jetcobotRobots = robots.filter(r => r.type === 'jetcobot')
  const connectedPinkyIds = pinkyRobots.filter(r => r.connected).map(r => r.robot_id)
  const connectedCount = robots.filter(r => r.connected).length

  // log WS state changes
  const prevWsRef = useRef(wsState)
  useEffect(() => {
    if (prevWsRef.current !== wsState) {
      addLog(`WS ${wsState}`, wsState === 'connected' ? 'ok' : 'err')
      prevWsRef.current = wsState
    }
  }, [wsState])

  // log robot connect/disconnect
  const prevConnRef = useRef({})
  useEffect(() => {
    robots.forEach(r => {
      const prev = prevConnRef.current[r.robot_id]
      if (prev !== undefined && prev !== r.connected) {
        addLog(`${r.robot_id} ${r.connected ? '연결됨' : '연결 끊김'}`,
               r.connected ? 'ok' : 'warn')
      }
      prevConnRef.current[r.robot_id] = r.connected
    })
  }, [robots])

  const wsColor = wsState === 'connected' ? COLORS.green : COLORS.red

  return (
    <div style={{ maxWidth: 480, margin: '0 auto', padding: '16px 12px', fontFamily: 'sans-serif' }}>
      {/* header */}
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16, gap: 8 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Moosinsa Fleet</h2>
        <span style={{ fontSize: 13, color: COLORS.gray, fontVariantNumeric: 'tabular-nums' }}>
          {clock}
        </span>
        <span style={{ marginLeft: 'auto', fontSize: 12, color: wsColor }}>
          WS: {wsState}
        </span>
        <span style={{ fontSize: 12, color: COLORS.gray }}>
          {connectedCount}/{robots.length} 연결
        </span>
      </div>

      {/* map */}
      <MapView robots={robots} />

      {/* pinky section */}
      <div style={{ marginBottom: 8, fontSize: 12, fontWeight: 700, color: COLORS.gray, textTransform: 'uppercase', letterSpacing: 1 }}>
        Pinky (Mobile)
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 16 }}>
        {pinkyRobots.map(r => <PinkyCard key={r.robot_id} robot={r} addLog={addLog} />)}
      </div>

      {/* jetcobot section */}
      <div style={{ marginBottom: 8, fontSize: 12, fontWeight: 700, color: COLORS.gray, textTransform: 'uppercase', letterSpacing: 1 }}>
        Jetcobot (Arm)
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        {jetcobotRobots.map(r => (
          <JetcobotCard
            key={r.robot_id}
            robot={r}
            pinkyIds={connectedPinkyIds.length > 0 ? connectedPinkyIds : PINKY_ROBOT_IDS}
          />
        ))}
      </div>

      {/* log */}
      <LogPanel logs={logs} />
    </div>
  )
}
