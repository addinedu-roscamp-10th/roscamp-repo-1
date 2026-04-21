import { BrowserRouter, Routes, Route } from 'react-router-dom';
import ProductDetailPage from './pages/ProductDetailPage';
import ShoeSearchResultPage from './pages/ShoeSearchResultPage';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<ProductDetailPage />} />
        <Route path="/search_result" element={<ShoeSearchResultPage />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;