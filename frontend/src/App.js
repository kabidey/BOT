import "@/App.css";
import "@/admin.css";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import Chat from "@/pages/Chat";
import Admin from "@/pages/Admin";

function App() {
  return (
    <div className="App">
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Chat />} />
          <Route path="/embed" element={<Chat embedded />} />
          <Route path="/admin" element={<Admin />} />
        </Routes>
      </BrowserRouter>
    </div>
  );
}

export default App;
