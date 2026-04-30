import { useEffect, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import './ProductDetailPage.css';

import ArrivalModal from "./ArrivalModal";
import TryOnModal from './TryOnModal';

type Product = {
  shoe_id: string;
  model: string;
  name: string;
  style: string;
  color: string;
  price: number;
  image_url?: string;
  sizes: number[];
  colors: string[];
};


type ShoeDetailInfo = {
  shoe_id: string;
  product_id: string;
  size: number;
  stock: string;
  warehouse_pos: string;
  color: string;
  image_url: string;
};


const seats = [1, 2, 3, 4];
const API = import.meta.env.VITE_API_URL;

// console.log("API:", API );

function getShoeId() {
  const params = new URLSearchParams(window.location.search);
  
  const direct = params.get('shoe_id');
  if (direct) return direct;

  const data = params.get('data');
  if (!data) return null;

  try {
    const parsed = JSON.parse(data);
    return parsed?.shoe_id || null;
  } catch {
    return null;
  }
}


export default function ProductDetailPage() {
  const navigate = useNavigate();

  const [product, setProduct] = useState<Product | null>(null);
  const [inventory, setInventory] = useState<ShoeDetailInfo[]>([]);
  
  const [selectedSize, setSelectedSize] = useState<number | null>(null);
  const [selectedColor, setSelectedColor] = useState<string | null>(null);
  const [displayImage, setDisplayImage] = useState('');
  const [imageError, setImageError] = useState(false);

  const [seat, setSeat] = useState(2);
  const [msg, setMsg] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // 신발 찾기  
  const [isFindDialogOpen, setIsFindDialogOpen] = useState(false);
  const [findInput, setFindInput] = useState('');
  const [findLoading, setFindLoading] = useState(false);


   // 좌석 정보 
  const [seatStatus, setSeatStatus] = useState<number[]>([0, 0, 0, 0]);

  // 시착 요청
  const [tryOnPopupOpen, setTryOnPopupOpen] = useState(false);
  const [tryOnLoading, setTryOnLoading] = useState(false);

  const [failModalOpen, setFailModalOpen] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);

  // 도착 팝업
  const [isArriveOpen, setIsArriveOpen] = useState(false);


  useEffect(() => {
    if (!API) return;

    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let closedByCleanup = false;

    const connect = () => {
      const wsBaseUrl = API.replace('http://', 'ws://').replace('https://', 'wss://');

      ws = new WebSocket(`${wsBaseUrl}/ws/amr`);

      ws.onopen = () => {
        console.log('AMR ws connected');
      };

      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.type === 'AMR_ARRIVE') {
          setTryOnPopupOpen(false);
          setIsArriveOpen(true);
        }
      };

      ws.onerror = () => {
        ws?.close();
      };

      ws.onclose = () => {
        console.log('AMR ws closed');

        if (!closedByCleanup) {
          retryTimer = setTimeout(connect, 1000);
        }
      };
    };

    connect();

    return () => {
      closedByCleanup = true;

      if (retryTimer) {
        clearTimeout(retryTimer);
      }

      ws?.close();
    };
  }, []);

  // seat info
  useEffect(() => {
    if (!API) return;

    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let closedByCleanup = false;

    const connect = () => {
      const wsBaseUrl = API.replace('http://', 'ws://').replace('https://', 'wss://');

      ws = new WebSocket(`${wsBaseUrl}/ws/seat`);

      ws.onopen = () => {
        console.log('seat ws connected:', `${wsBaseUrl}/ws/seat`);
      };

      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        console.log('seat ws message:', data);

        if (data.type === 'SEAT_UPDATE') {
          setSeatStatus(data.data);
        }
      };

      ws.onerror = (e) => {
        console.log('seat ws error:', e);
        ws?.close();
      };

      ws.onclose = () => {
        console.log('seat ws closed');

        if (!closedByCleanup) {
          retryTimer = setTimeout(connect, 1000);
        }
      };
    };

    connect();

    return () => {
      closedByCleanup = true;

      if (retryTimer) {
        clearTimeout(retryTimer);
      }

      ws?.close();
    };
  }, []);

  useEffect(() => {
    const id = getShoeId();
    if (!id) {
      setError('QR 데이터에서 상품코드를 찾을 수 없습니다.');
      setLoading(false);
      return;
    }

    const fetchProduct = async () => {
      try {
        
        const res = await fetch(
          `${API}/find_shoe?data=${encodeURIComponent(
            JSON.stringify({ shoe_id: id })
          )}`,{
            method: "POST",
          }
        );

        if (!res.ok) {
          const text = await res.text();
          throw new Error(`상품 조회 실패 (${res.status}) ${text}`);
        }

        const data = await res.json();
        
        const parsedSizes = typeof data.sizes === 'string' ? JSON.parse(data.sizes) : data.sizes;
        const parsedColors = typeof data.colors === 'string' ? JSON.parse(data.colors) : data.colors;
        setDisplayImage(`${API}${data.image_url}`);

        setProduct({
          ...data,
          name: data.model,
          sizes: parsedSizes,
          colors: parsedColors,
        });
      } catch (e) {
        console.error(e);
        setError('상품 정보를 불러오지 못했습니다.');
      } finally {
        setLoading(false);
      }
    };

    fetchProduct();
  }, []);

  useEffect(()=>{   
    if (!product?.shoe_id) return; // (product 아직 없을때 방지)
    const fetchInventory = async () => {
      try {
        const res = await fetch(
          `${API}/find_shoe_information?data=${encodeURIComponent(
            JSON.stringify({ shoe_id: product.shoe_id })
          )}`,
          {
            method: "POST",
          }
        );

        if (!res.ok) {
          const text = await res.text();
          throw new Error(`재고 조회 실패 (${res.status}) ${text}`);
        }

        const data = await res.json();
        setInventory(data); //배열 그대로 넣기
        console.log("inventory Data: ", data);

        if (data.length > 0) {
          const available = data.filter(
            (item: ShoeDetailInfo) => Number(item.stock) === 1
          );

          const target = available.length > 0 ? available[0] : data[0];

          setSelectedSize(target.size);
          setSelectedColor(target.color);

          if (target.image_url) {
            setDisplayImage(`${API}${target.image_url}`);
          }
        }

      } catch (e) {
        console.error(e);
      }
    };

    fetchInventory();
  },[product]);

  useEffect(() => {
    setImageError(false);
  }, [displayImage]);

  useEffect(() => {
    console.log('displayImage:', displayImage);
  }, [displayImage]);

  useEffect(() => {
    if (isFindDialogOpen) {
      document.body.classList.add('modal-open');
    } else {
      document.body.classList.remove('modal-open');
    }

    return () => {
      document.body.classList.remove('modal-open');
    };
  }, [isFindDialogOpen]);


   // msg 
  useEffect(() => {
    if (!msg) return;

    const timer = setTimeout(() => {
      setMsg('');
    }, 5000);

    return () => clearTimeout(timer);
  }, [msg]);

  const normalizeColor = (value: string | null | undefined) => {
    return String(value ?? '').trim().toLowerCase();
  };

  const hasStock = (size: number, color: string | null) => {
    return inventory.some(
      (item) =>
        item.shoe_id === product?.shoe_id &&
        Number(item.size) === Number(size) &&
        normalizeColor(item.color) === normalizeColor(color) &&
        Number(item.stock) === 1
    );
  };

  const hasColorStock = (color: string) => {
    return inventory.some(
      (item) =>
        item.shoe_id === product?.shoe_id &&
        normalizeColor(item.color) === normalizeColor(color) &&
        Number(item.stock) === 1
    );
  };

  const handleSizeClick = (size: number) => {
    if (!selectedColor) return;

    const matched = inventory.find(
      (item) =>
        item.shoe_id === product?.shoe_id &&
        Number(item.size) === Number(size) &&
        normalizeColor(item.color) === normalizeColor(selectedColor) &&
        Number(item.stock) === 1
    );

    if (!matched) return;

    setSelectedSize(Number(matched.size));

    if (matched.image_url) {
      setDisplayImage(`${API}${matched.image_url}`);
    }
  };

  const handleColorClick = (color: string) => {
    const availableItems = inventory
      .filter(
        (item) =>
          item.shoe_id === product?.shoe_id &&
          normalizeColor(item.color) === normalizeColor(color) &&
          Number(item.stock) === 1
      )
      .sort((a, b) => Number(a.size) - Number(b.size));

    if (availableItems.length === 0) {
      setSelectedColor(color);
      setSelectedSize(null);
      return;
    }

    const target = availableItems[0];
      
    setSelectedColor(target.color);
    setSelectedSize(Number(target.size));

    if (target.image_url) {
      setDisplayImage(`${API}${target.image_url}`);
    }
  };

  const selectedItem = inventory.find(
    (item) =>
      item.shoe_id === product?.shoe_id &&
      Number(item.size) === Number(selectedSize) &&
      normalizeColor(item.color) === normalizeColor(selectedColor)
  );

  const productCount = selectedItem
    ? inventory.filter(
        (item) =>
          item.shoe_id === product?.shoe_id &&
          item.product_id === selectedItem.product_id &&
          Number(item.stock) === 1
      ).length
    : 0;

  //2026 04 29 
  // const handleTryOnRequest = async () => {
  //   try {

  //     const selectedItem = inventory.find((item) => {
  //       return item.size === selectedSize && item.color === selectedColor;
  //     });

  //     if (!selectedItem) {
  //       setMsg('사이즈와 색상을 선택해주세요.');
  //       return;
  //     }

  //     setTryOnPopupOpen(true);
  //     setFailModalOpen(false);

  //     const tryOnRes = await fetch(`${API}/tryon/request`, {
  //       method: 'POST',
  //       headers: {
  //         'Content-Type': 'application/json',
  //       },
  //       body: JSON.stringify({
  //         product_id: selectedItem.product_id,
  //         seat_id: `seat_${seat}`,
  //       }),
  //     });

  //     const tryOnData = await tryOnRes.json();
  //     console.log('try-on:', tryOnData);

  //     if (!tryOnRes.ok || !tryOnData.success) {
  //       alert('시착 요청 접수 실패');
  //       setTryOnPopupOpen(false);
  //       setFailModalOpen(true); 
  //       return;
  //     }

  //     setMsg(
  //       `시착 요청 완료: ${product?.model} / ${selectedSize ?? '-'} / ${selectedColor ?? '-'} / 좌석 ${seat}`
  //     );
  //   } catch (error) {
  //     console.error(error);
  //     setMsg('요청 중 오류 발생');
  //     setTryOnPopupOpen(false);
  //     setFailModalOpen(true); 
  //   }

  // };
  //2026 04 29 

  /* ============================================================
   * === 시착 시나리오 (Scene 2) 임시 코드 — 담당자 인계용 ===
   * 작성일: 2026-04-27
   * 범위: TC 2-06 (모바일 시착 요청), TC 2-19 (수령 완료)
   *
   * 담당자가 할 일:
   *   1. robot_id 하드코딩(sshopy2) → FMS에서 사용가능 로봇 자동 선택
   *   2. product_id 변환: 현재 product.model 사용 → 정식 product_id 사용
   *   3. WS 재연결 로직 추가 (현재 일회성)
   *   4. 에러 응답 처리 강화 (HTTP 409 좌석사용중, 503 미연결 등)
   *   5. 수령완료 후 페이지 전환 또는 후속 처리
   *
   * 동작:
   *   - 시착 요청: POST {API}/tryon/request {product_id, color, size, seat_id, robot_id}
   *   - 도착 감지: WS {API}/ws/amr → AMR_ARRIVE → ArrivalModal 자동 표시
   *   - 수령 완료: ArrivalModal onClose에서 POST {API}/pickup/complete
   * ============================================================ */
  const TRYON_ROBOT_ID = 'sshopy1';   // 임시 하드코딩

  const handleTryOnRequest = async () => {
    if (!API) {
      setMsg('API_URL 미설정');
      return;
    }
    if (!product) {
      setMsg('상품 정보 없음');
      return;
    }
    try {
      const res = await fetch(`${API}/tryon/request`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          product_id: String(product.model ?? ''),
          color:      selectedColor ?? null,
          size:       selectedSize != null ? String(selectedSize) : null,
          seat_id:    seat,
          robot_id:   TRYON_ROBOT_ID,
        }),
      });
      if (!res.ok) {
        const text = await res.text();
        setMsg(`시착 요청 실패 (${res.status}): ${text}`);
        return;
      }
      setMsg(
        `시착 요청 완료: ${product?.model} / ${selectedSize ?? '-'} / ${selectedColor ?? '-'} / 좌석 ${seat}`
      );
    } catch (error) {
      console.error(error);
      setMsg('시착 요청 중 오류 발생');
    }
  };

  // 수령 완료 (ArrivalModal "수령 완료" 버튼 클릭 시)
  const handlePickupComplete = async () => {
    setIsArriveOpen(false);
    if (!API) return;
    try {
      const res = await fetch(`${API}/pickup/complete?robot_id=${TRYON_ROBOT_ID}`, {
        method: 'POST',
      });
      if (!res.ok) {
        const text = await res.text();
        setMsg(`수령 완료 처리 실패 (${res.status}): ${text}`);
        return;
      }
      setMsg('수령 완료 — 로봇이 회수존으로 이동합니다');
    } catch (error) {
      console.error(error);
      setMsg('수령 완료 요청 중 오류 발생');
    }
  };
  /* === 시착 시나리오 임시 코드 끝 ============================================ */

  // 신발 찾기 외부함수 
  const handleFindShoeRequest = async (shoe_id: string = '') => {  
    
    try {        
      const res = await fetch(
          `${API}/find_shoe?data=${encodeURIComponent(
            JSON.stringify({ "shoe_id": shoe_id })
          )}`,{
            method: "POST",
          }  
      );
  
      if (!res.ok) {
        const text = await res.text();  
        throw new Error(`상품 조회 실패 (${res.status}) ${text}`);
      }
        
      const data = await res.json();  
      const parsedSizes = typeof data.sizes === 'string' ? JSON.parse(data.sizes) : data.sizes;
      const parsedColors = typeof data.colors === 'string' ? JSON.parse(data.colors) : data.colors;
      setDisplayImage(`${API}${data.image_url}`);
  
      setProduct({
        ...data,  
        name: data.model,  
        sizes: parsedSizes,  
        colors: parsedColors,  
      });
    } catch (e) {  
      console.error(e);  
      setError('상품 정보를 불러오지 못했습니다.');  
    } finally {  
      setLoading(false);   
    } 
  };

  const handleFindAllShoeRequest = async () => {  
    
    try {        
      const res = await fetch(     
        `${API}/find_shoe?data=${encodeURIComponent(   
          JSON.stringify({ "shoe_id": '' })
        )}`,{   
          method: "POST",  
        }  
      );
  
      if (!res.ok) {
        const text = await res.text();  
        throw new Error(`상품 조회 실패 (${res.status}) ${text}`);  
      }

      const data = await res.json();
        
      // 여기 추가  
      const filteredData = data.filter((item: any) => {  
        return typeof item.shoe_id === 'string' && item.shoe_id.trim().length > 0;
      });
      
      if (Array.isArray(filteredData) && filteredData.length > 0) {
        navigate('/search_result', {
          state: { shoes: filteredData },
        });
        return;
      }

    } catch (e) {
      console.error(e);
      setError('상품 정보를 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  };


  const handleSearchRequest = async () => {
    if (!findInput.trim()) {
      setMsg('검색어를 입력해주세요');
      setIsFindDialogOpen(false);
      return;
    }
    try {
      setFindLoading(true);
      setMsg('');

      const res = await fetch(`${API}/search`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          keyword: findInput,
        }),
      });

      const text = await res.text();
      console.log("handleSearchRequest response text:", text);

      if (!res.ok) {
        throw new Error(`검색 실패 (${res.status}) ${text}`);
      }

      const data = JSON.parse(text);
      console.log('find shoe response:', data);

      // 1) m_llm 응답 형식: { results: [...], count: n, debug: {...} }
      if (Array.isArray(data.results)) {
        if (data.results.length === 0) {
          setMsg('검색 결과가 없습니다.');
          return;
        }

        navigate('/search_result', {
          state: { shoes: data.results },
        });
        setIsFindDialogOpen(false);
        setFindInput('');
        return;
      }

      // 2) 기존 배열 응답도 대응
      if (Array.isArray(data)) {
        if (data.length === 0) {
          setMsg('검색 결과가 없습니다.');
          return;
        }

        navigate('/search_result', {
          state: { shoes: data },
        });
        setIsFindDialogOpen(false);
        setFindInput('');
        return;
      }

      // 객체이면 → 상세 페이지로
      const parsedSizes = typeof data.sizes === 'string' ? JSON.parse(data.sizes) : data.sizes;
      const parsedColors = typeof data.colors === 'string' ? JSON.parse(data.colors) : data.colors;        
      setDisplayImage(`${API}${data.image_url}`);

      setProduct({  
        ...data,
        name: data.model,        
        sizes: parsedSizes,        
        colors: parsedColors,
      });

      setIsFindDialogOpen(false);
      setFindInput('');
    } catch (error) {
      console.error(error);
      setMsg('키워드 검색 요청 중 오류 발생');
    } finally {
      setFindLoading(false);
    }
  };

  if (loading) {
    return <div className="page-container">로딩중...</div>;
  }

  if (error || !product) {
    return (
      <div className="page-container">
        <div className="main-card">
          <div className="header-title">오류</div>
          <div className="header-sub">{error}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="page-container">

      {/* 시착 진행중 */}
      <TryOnModal
        open={tryOnPopupOpen}
        onClose={() => setTryOnPopupOpen(false)}
        image={displayImage}
        productName={product?.name}
        size={selectedSize}
        color={selectedColor}
        // seat={seat}
      />

      {/* 도착 */}
      <ArrivalModal
        open={isArriveOpen}
        onClose={() => setIsArriveOpen(false)}
      />

      {/* 실패 */}
      <ArrivalModal
        open={failModalOpen}
        onClose={() => setFailModalOpen(false)}
        type="fail"
      />
      {/* ✅ 여기 넣기 (main-card 위) */}
      {isFindDialogOpen && (
        <div className="dialog-overlay">
          <div className="dialog-box">
            <div className="dialog-title">신발 찾기</div>
            <div className="dialog-subtitle">검색어를 입력해주세요.</div>

            <input
              type="text"
              value={findInput}
              onChange={(e) => setFindInput(e.target.value)}
              // placeholder="예: U992"
              className="dialog-input"
            />

            <div className="dialog-btn-row">
              <button
                className="dialog-cancel-btn"
                onClick={() => {
                  setIsFindDialogOpen(false);
                  setFindInput('');
                }}
              >
                취소
              </button>

              <button
                className="dialog-confirm-btn"
                onClick={handleSearchRequest}
                disabled={findLoading}
              >
                {findLoading ? '전송 중...' : '확인'}
              </button>
            </div>
          </div>
        </div>
      )}


      <div className="main-card">
        <div className="product-card">
          <div className="product-thumb">
             {!imageError && displayImage ? (
                <img
                  key={displayImage}
                  src={displayImage}
                  alt={product.name}
                  className="product-image"
                  onError={() => setImageError(true)}
                />
              ) : (
                <div className="product-image-fallback">
                  👟
                </div>
              )}
          </div>
          <div className="product-info">
            <div className="product-name">{product.name}</div>
            <div className="product-price">
              ₩{product?.price?.toLocaleString('ko-KR') ?? '-'}
            </div>

            <div className="product-stock">
              재고: {productCount}개
            </div>
          </div>
        </div>

        <div className="section">
          <div className="section-title">사이즈 선택</div>
          <div className="size-list">
            {product.sizes.map((size) => {
              const enabled = selectedColor ? hasStock(size, selectedColor) : false;

              return (
                <button
                  key={size}
                  disabled={!enabled}
                  className={`size-btn ${
                    Number(selectedSize) === Number(size) ? 'selected' : ''
                  }`}
                  onClick={() => handleSizeClick(size)}
                >
                  {size}
                </button>
              );
            })}
          </div>
        </div>

        <div className="section">
          <div className="section-title">색상 선택</div>
          <div className="color-list">
            {product.colors.map((color) => {
              const enabled = hasColorStock(color);

              return (
                <button
                  key={color}
                  disabled={!enabled}
                  className={`color-circle ${
                    normalizeColor(selectedColor) === normalizeColor(color)
                      ? 'selected'
                      : ''
                  }`}
                  style={{ backgroundColor: color }}
                  onClick={() => handleColorClick(color)}
                >
                  {color}
                </button>
              );
            })}
          </div>
        </div>

        <div className="section">
          <div className="section-title">시착 좌석 선택</div>
          <div className="seat-container">
            <div className="seat-map">
              <div className="kiosk-tag">키오스크</div>

              <div className="display-grid">
                {Array.from({ length: 6 }).map((_, idx) => (
                  <div key={idx} className="display-box">
                    진열대
                  </div>
                ))}
              </div>

              <div className="seat-zone-label">시착 구역</div>
              <div className="seat-area">
                {[1, 4, 2, 3].map((s) => {
                  const idx = seats.indexOf(s); 
                  const occupied = seatStatus[idx] === 1;

                  return (
                    <button
                      key={s}
                      disabled={occupied}
                      className={`seat-btn 
                        ${seat === s ? 'active' : ''} 
                        ${occupied ? 'occupied' : ''}
                      `}
                      onClick={() => {
                        if (!occupied) setSeat(s);
                      }}
                    >
                      <span className="seat-number">{s}</span>

                      {occupied && (
                        <span className="seat-label">사용중</span>
                      )}
                    </button>
                  );
                })}
              </div>

              <div className="seat-labels"></div>
              <div className="entrance-tag">입구</div>
            </div>
          </div>
        </div>

        {msg ? <div className="message-box">{msg}</div> : null}

        <div className="btn-row">
          <button
            className="action-btn"
            onClick={handleTryOnRequest}
          >
            시착 요청
          </button>
          <button
            className="action-btn"
            onClick={() => {setIsFindDialogOpen(true);setMsg('')}}
          >
            신발 찾기
          </button>

          <button
            className="action-btn"
            onClick={()=>handleFindAllShoeRequest()}
          >
            전체 신발 보기
          </button>

        </div>
      </div>
    </div>
  );
}
