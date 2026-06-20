import { Route, Routes } from 'react-router-dom'
import Library from './pages/Library'
import Reader from './pages/Reader'

function App() {
  return (
    <div className="min-h-screen bg-slate-950">
      <Routes>
        <Route path="/" element={<Library />} />
        <Route path="/books/:bookId" element={<Reader />} />
      </Routes>
    </div>
  )
}

export default App
