import { usePolling } from '../api/client'

interface Source {
  source: string
  name: string
  collected: number
  official_total: number | null
  license: string
  auto_updates: boolean
  note: string
  role: 'designation' | 'fishing' | 'detention'
  imported_at: string | null
}

const ROLE_TAG: Record<string, { label: string; cls: string }> = {
  designation: { label: 'sanctions / ban', cls: 'shadow_fleet' },
  fishing: { label: 'IUU fishing', cls: 'iuu_fishing' },
  detention: { label: 'detention (corroboration)', cls: 'closed' },
}
interface SourcesData {
  sources: Source[]
  unique_vessels: number
  sanctioned_unique: number
  global_fleet_estimate: number
}

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString('en-GB', { timeZone: 'UTC' }) + ' UTC' : 'never'
}

export default function Sources() {
  const { data, error } = usePolling<SourcesData>('/risklists/status', 60_000)
  const sources = data?.sources ?? []
  const totalCollected = sources.reduce((a, s) => a + s.collected, 0)

  return (
    <div className="page">
      <h1>Sources</h1>
      <p className="sub">
        The sanctions and watchlists feeding the engine. Every listed vessel is
        matched against the live global feed by IMO number; whichever transmit get
        added to the watchlist automatically. All sources refresh daily from their
        live source (UANI merges from the Internet Archive). Sanctions and port-ban
        lists count toward coverage; port-control detentions are corroboration only.
      </p>

      {error && <p className="error">{error}</p>}

      <div style={{ display: 'flex', gap: '2.5rem', margin: '1.5rem 0', flexWrap: 'wrap' }}>
        <div>
          <div className="stat-num" style={{ color: 'var(--shadow-fleet)' }}>{data?.sanctioned_unique?.toLocaleString() ?? '-'}</div>
          <div className="stat-lab">sanctioned / banned vessels (deduplicated)</div>
        </div>
        <div>
          <div className="stat-num">~{(data?.global_fleet_estimate ?? 1400).toLocaleString()}</div>
          <div className="stat-lab">est. global shadow fleet (RUSI / Windward)</div>
        </div>
        <div><div className="stat-num">{sources.length}</div><div className="stat-lab">sources</div></div>
        <div>
          <div className="stat-num" style={{ color: 'var(--muted)' }}>{data?.unique_vessels?.toLocaleString() ?? '-'}</div>
          <div className="stat-lab">+ incl. port-control detentions</div>
        </div>
      </div>
      <p className="sub" style={{ marginTop: '-0.5rem', fontSize: 13 }}>
        The <strong>{data?.sanctioned_unique?.toLocaleString()}</strong> is the real
        coverage figure: unique vessels on a sanctions or port-ban list. It exceeds the
        ~1,400 Russian shadow-fleet estimate because it also spans Iran, Venezuela and
        North Korea. The grey number adds port-control detention lists (Tokyo, Black Sea,
        Abuja MoU) - substandard ships that corroborate other signals but are not
        themselves designations.
      </p>

      <table>
        <thead>
          <tr>
            <th>List</th><th>Role</th><th>Vessels</th><th>Coverage</th>
            <th>Licence</th><th>Refreshed</th>
          </tr>
        </thead>
        <tbody>
          {sources.map((s) => {
            const pct = s.official_total ? Math.round((s.collected / s.official_total) * 100) : null
            const partial = pct != null && pct < 100
            const tag = ROLE_TAG[s.role]
            return (
              <tr key={s.source}>
                <td>{s.name}<div style={{ color: 'var(--muted)', fontSize: 11, marginTop: 2, maxWidth: 280 }}>{s.note}</div></td>
                <td><span className={`tag ${tag.cls}`}>{tag.label}</span></td>
                <td className="mono">{s.collected.toLocaleString()}</td>
                <td style={{ minWidth: 110 }}>
                  {partial ? (
                    <div className="bar"><div className="bar-fill" style={{ width: `${pct}%` }} /><span>{pct}%</span></div>
                  ) : (
                    <span style={{ color: 'var(--watch-other)', fontSize: 12 }}>✓ complete</span>
                  )}
                </td>
                <td className="mono" style={{ fontSize: 11 }}>{s.license}</td>
                <td className="mono" style={{ fontSize: 11 }}>{s.imported_at ? fmtDate(s.imported_at).slice(0, 17) : '-'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>

      <p className="sub" style={{ marginTop: '1.5rem', fontSize: 13 }}>
        &quot;Complete&quot; means we ingest the source&apos;s entire current list. Where a
        published total is shown, it&apos;s the count that body last reported - our number
        can slightly exceed it when our copy is newer (or, for UANI, includes
        delisted historicals). A percentage bar appears only when we hold less than the
        published figure. Every source refreshes automatically each day.
      </p>
    </div>
  )
}
