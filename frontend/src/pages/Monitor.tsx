import { useState } from 'react'
import Watchlist from './Watchlist'
import Suggestions from './Suggestions'
import Events from './Events'

type Tab = 'watchlist' | 'suggestions' | 'events'

// One nav destination that fronts the three monitoring views as tabs, the same
// pattern as the Admin page. Each tab renders its existing page component.
export default function Monitor() {
  const [tab, setTab] = useState<Tab>('watchlist')

  return (
    <>
      <div className="page monitor-tabbar">
        <div className="tabs" role="tablist">
          <button
            role="tab"
            aria-selected={tab === 'watchlist'}
            className={tab === 'watchlist' ? 'active' : ''}
            onClick={() => setTab('watchlist')}
          >
            Watchlist
          </button>
          <button
            role="tab"
            aria-selected={tab === 'suggestions'}
            className={tab === 'suggestions' ? 'active' : ''}
            onClick={() => setTab('suggestions')}
          >
            Suggestions
          </button>
          <button
            role="tab"
            aria-selected={tab === 'events'}
            className={tab === 'events' ? 'active' : ''}
            onClick={() => setTab('events')}
          >
            Events
          </button>
        </div>
      </div>

      {tab === 'watchlist' && <Watchlist />}
      {tab === 'suggestions' && <Suggestions />}
      {tab === 'events' && <Events />}
    </>
  )
}
