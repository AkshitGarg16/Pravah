import Header from './components/Header'
import SearchBar from './components/SearchBar'
import InfraBar from './components/InfraBar'
import LightPanel from './components/LightPanel'
import InfraPanel from './components/InfraPanel'
import MapView from './components/MapView'
import HealthPanel from './components/HealthPanel'
import AlertPanel from './components/AlertPanel'
import InfraSummary from './components/InfraSummary'
import { useDashboardStore } from './store/useDashboardStore'

export default function App() {
  const infraMode = useDashboardStore((s) => s.infraMode)

  return (
    <div
      className="h-screen w-screen overflow-hidden bg-surface grid"
      style={{
        gridTemplateRows: '52px 48px 1fr',
        gridTemplateColumns: '260px 1fr 290px',
        gridTemplateAreas: `
          "header header header"
          "search search search"
          "left map right"
        `,
      }}
    >
      <Header />
      {infraMode ? <InfraBar /> : <SearchBar />}

      <div style={{ gridArea: 'left' }} className="min-h-0 overflow-hidden">
        {infraMode ? <InfraPanel /> : <LightPanel />}
      </div>

      <MapView />

      <aside
        style={{ gridArea: 'right' }}
        className="bg-card border-l border-edge flex flex-col min-h-0 overflow-hidden"
      >
        {infraMode ? (
          <InfraSummary />
        ) : (
          <>
            <HealthPanel />
            <AlertPanel />
          </>
        )}
      </aside>
    </div>
  )
}
