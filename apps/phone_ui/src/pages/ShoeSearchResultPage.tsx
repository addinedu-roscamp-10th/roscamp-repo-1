import { useLocation, useNavigate } from 'react-router-dom';
import './ShoeSearchResultPage.css';

type ShoeItem = {
  id: number;
  brand: string;
  model: string;
  image_url?: string | null;
  price: number;
  shoe_id: string;
  sizes: number[] | string;
  colors: string[] | string;
  tags?: string;
};

const API = `http://192.168.0.20:8000`;


// const location = useLocation();
// const shoes = location.state?.shoes || [];

// console.log('search result shoes:', shoes);

function parseSizes(value: ShoeItem['sizes']): string[] {
  if (Array.isArray(value)) {
    return value.map(String);
  }

  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed.map(String) : [];
    } catch {
      return [];
    }
  }

  return [];
}

function parseColors(value: ShoeItem['colors']): string[] {
  if (Array.isArray(value)) {
    return value;
  }

  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  return [];
}

export default function ShoeSearchResultPage() {
  const location = useLocation();
  const navigate = useNavigate();

  const shoes: ShoeItem[] = location.state?.shoes ?? [];

  return (
    <div className="search-page-container">
      <div className="search-main-card">
        <div className="search-page-title">전체 신발 보기</div>
        <div className="search-page-sub">총 {shoes.length}개의 상품</div>

        <div className="search-result-list">
          {shoes.map((item) => {
            const sizes = parseSizes(item.sizes);
            const colors = parseColors(item.colors);

            return (
              <button
                key={item.id}
                className="shoe-result-card"
                onClick={() =>
                  navigate(`/?shoe_id=${encodeURIComponent(item.shoe_id)}`)
                }
              >
                <div className="shoe-result-thumb">
                  {item.image_url ? (
                    <img
                      src={`${API}${item.image_url}`}
                      alt={item.model}
                      className="shoe-result-image"
                    />
                  ) : (
                    <div className="shoe-result-fallback">👟</div>
                  )}
                </div>

                <div className="shoe-result-info">
                  <div className="shoe-result-brand">{item.brand}</div>
                  <div className="shoe-result-name">{item.model}</div>
                  <div className="shoe-result-code">{item.shoe_id}</div>

                  <div className="shoe-result-row">
                    <span className="label">색상</span>
                    <span className="value">
                      {colors.length > 0 ? colors.join(', ') : '-'}
                    </span>
                  </div>

                  <div className="shoe-result-row">
                    <span className="label">사이즈</span>
                    <span className="value">
                      {sizes.length > 0 ? sizes.join(', ') : '-'}
                    </span>
                  </div>

                  <div className="shoe-result-row">
                    <span className="label">설명</span>
                    <span className="value">{item.tags || '-'}</span>
                  </div>

                  <div className="shoe-result-price">
                    ₩{Number(item.price).toLocaleString('ko-KR')}
                  </div>
                </div>
              </button>
            );
          })}

          {shoes.length === 0 && (
            <div className="empty-result-box">조회된 신발이 없습니다.</div>
          )}
        </div>
      </div>
    </div>
  );
}