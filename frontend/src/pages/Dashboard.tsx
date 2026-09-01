import FloodMap from '../map/FloodMap'
function Dashboard() {
  return (
    <main className="dashboard">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">DISASTER MANAGEMENT SYSTEM</p>
          <h1>Flood-Twin</h1>
          <p className="subtitle">
            Dam-break and flash-flood simulation platform
          </p>
        </div>

        <div className="system-status">
          <span className="status-dot" />
          System Operational
        </div>
      </header>

      <section className="dashboard-grid">
        <aside className="scenario-panel">
          <h2>Scenario</h2>

          <label>
            River
            <select defaultValue="">
              <option value="" disabled>
                Select river
              </option>
              <option>Rishi Ganga</option>
              <option>Alaknanda</option>
              <option>Kosi</option>
            </select>
          </label>

          <label>
            Dam / Reservoir
            <select defaultValue="">
              <option value="" disabled>
                Select dam
              </option>
              <option>Demo Dam</option>
            </select>
          </label>

          <label>
            Failure Scenario
            <select defaultValue="full">
              <option value="normal">Normal release</option>
              <option value="partial">Partial breach</option>
              <option value="full">Full breach</option>
              <option value="extreme">Extreme rainfall + breach</option>
            </select>
          </label>

          <label>
            Reservoir Level
            <input type="range" min="0" max="100" defaultValue="75" />
            <span className="range-value">75%</span>
          </label>

          <button type="button" className="simulate-button">
            Run Simulation
          </button>
        </aside>

<section className="map-panel">
  <FloodMap />
</section>

      <section className="metrics">
        <article>
          <span>Flood Area</span>
          <strong>—</strong>
          <small>km²</small>
        </article>

        <article>
          <span>Maximum Depth</span>
          <strong>—</strong>
          <small>m</small>
        </article>

        <article>
          <span>Maximum Velocity</span>
          <strong>—</strong>
          <small>m/s</small>
        </article>

        <article>
          <span>Population Exposed</span>
          <strong>—</strong>
          <small>people</small>
        </article>
      </section>
    </main>
  )
}

export default Dashboard