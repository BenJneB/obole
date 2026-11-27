import { NavLink, Route, Routes } from 'react-router-dom'
import { useRegisterSW } from 'virtual:pwa-register/react'
import AddItem from './pages/AddItem.tsx'
import Collection from './pages/Collection.tsx'
import ItemPage from './pages/ItemPage.tsx'
import Settings from './pages/Settings.tsx'
import StatsPage from './pages/StatsPage.tsx'

function UpdateBanner() {
  const {
    needRefresh: [needRefresh],
    updateServiceWorker,
  } = useRegisterSW()
  if (!needRefresh) return null
  return (
    <div className="banner">
      Nouvelle version disponible.
      <button className="btn small" onClick={() => updateServiceWorker(true)}>
        Mettre à jour
      </button>
    </div>
  )
}

export default function App() {
  return (
    <div className="app">
      <UpdateBanner />
      <main className="content">
        <Routes>
          <Route path="/" element={<Collection />} />
          <Route path="/ajouter" element={<AddItem />} />
          <Route path="/objet/:id" element={<ItemPage />} />
          <Route path="/stats" element={<StatsPage />} />
          <Route path="/reglages" element={<Settings />} />
          <Route path="*" element={<p className="empty">Page introuvable.</p>} />
        </Routes>
      </main>
      <nav className="tabbar">
        <NavLink to="/" end>
          <span aria-hidden>▦</span>Collection
        </NavLink>
        <NavLink to="/ajouter">
          <span aria-hidden>＋</span>Ajouter
        </NavLink>
        <NavLink to="/stats">
          <span aria-hidden>▤</span>Stats
        </NavLink>
        <NavLink to="/reglages">
          <span aria-hidden>⚙</span>Réglages
        </NavLink>
      </nav>
    </div>
  )
}
