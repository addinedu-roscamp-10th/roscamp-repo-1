import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import './ProductDetailPage.css';

// type ProductColor = {
//   name: string;
//   value: string;
// };

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
// const API = `http://${window.location.hostname}:8000`;
const API = `http://192.168.0.20:8000`;

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
    console.log("use effect ================== product ");
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

        if (data.length > 0) {
          const first = data[0];
          setSelectedSize(first.size);
          setSelectedColor(first.color);

          if (first.image_url) {
            setDisplayImage(`${API}${first.image_url}`);
          }

          console.log('product image_url:', data.image_url);
          console.log('inventory first:', first);
          console.log('inventory first image url:', `${API}${first.image_url}`);

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

  const handleSizeClick = (size: number) => {
    if (!selectedColor) return;

    const matched = inventory.find(
      (item) => item.size === size && item.color === selectedColor
    );

    if (!matched) return;

    setSelectedSize(size);

    if (matched.image_url) {
      setDisplayImage(`${API}${matched.image_url}`);
    }
  };

  const handleColorClick = (color: string) => {
    if (!selectedSize) return;

    const matched = inventory.find(
      (item) => item.color === color && item.size === selectedSize
    );
    
    if (!matched) return;

    setSelectedColor(color);

    if (matched.image_url) {
      setDisplayImage(`${API}${matched.image_url}`);
    }
  };

  const handleTryOnRequest = async () => {
    try {
      const tryOnRes = await fetch(`${API}/try-on-request`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          model: product?.model,
          robot_name: 'shoppy1',
        }),
      });

      const tryOnData = await tryOnRes.json();
      console.log('try-on:', tryOnData);

      if (!tryOnData.success) {
        alert('시착 요청 접수 실패');
        return;
      }

      const robotRes = await fetch(`${API}/robot/forward`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          robot_name: 'shoppy1',
          speed: 0.2,
          duration: 1.0,
        }),
      });

      const robotData = await robotRes.json();

      if (robotData.success) {
        setMsg(
          `시착 요청 완료: ${product?.model} / ${selectedSize ?? '-'} / ${
            selectedColor ?? '-'
          } / 좌석 ${seat}`
        );
      } else {
        setMsg('시착 요청은 되었지만 로봇 이동 실패');
      }
    } catch (error) {
      console.error(error);
      setMsg('요청 중 오류 발생');
    }
  };

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
        console.log(data);
        
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
        console.log(data);
      
        if (Array.isArray(data) && data.length > 0) {
          navigate('/search_result', {
            state: { shoes: data },
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

    console.log('handleSearchRequest --------------------');

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


      if (!res.ok) {
        const text = await res.text();
        throw new Error(`검색 실패 (${res.status}) ${text}`);
      }

      const data = await res.json();
      console.log('find shoe response:', data);

      // 배열이면 → 검색 결과 리스트 페이지
      if (Array.isArray(data)) {
      console.log("================== array");
        if (data.length === 0) {
          setMsg('검색 결과가 없습니다.');
          return;
        }

        navigate('/search_result', {
          state: { shoes: data },
        });
        return;
      }

      // 객체이면 → 상세 페이지로
      console.log("================== object" , data);
      const parsedSizes = typeof data.sizes === 'string' ? JSON.parse(data.sizes) : data.sizes;
      const parsedColors = typeof data.colors === 'string' ? JSON.parse(data.colors) : data.colors;        
      setDisplayImage(`${API}${data.image_url}`);

      setProduct({
          ...data,
          name: data.model,
          sizes: parsedSizes,
          colors: parsedColors,
      });

      // navigate('/product_detail', {
      //   state: { product: data },
      // });


      // setMsg(`키워드 검색 요청 완료: ${data.model ?? data.shoe_id ?? findInput}`);
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
          </div>
        </div>

        <div className="section">
          <div className="section-title">사이즈 선택</div>

          <div className="size-list">
            {product?.sizes.map((size) => {
              const enabled = selectedColor
                ? inventory.some(
                    (item) => item.size === size && item.color === selectedColor
                  )
                : false;

              return (
                <button
                  key={size}
                  disabled={!enabled}
                  // className={selectedSize === size ? 'selected' : ''}
                  className={`size-btn ${selectedSize === size ? 'selected' : ''}`}
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
            {product?.colors.map((color) => {
              const enabled = selectedSize
                ? inventory.some(
                    (item) => item.color === color && item.size === selectedSize
                  )
                : false;

              return (
                <button
                  key={color}
                  disabled={!enabled}
                  className={`color-circle ${selectedColor === color ? 'selected' : ''}`}
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
                {seats.map((s) => (
                  <button
                    key={s}
                    className={`seat-btn ${seat === s ? 'active' : ''}`}
                    onClick={() => setSeat(s)}
                  >
                    {s}
                  </button>
                ))}
              </div>

              <div className="seat-labels">
                {/* <span>좌석 1</span>
                <span>좌석 2</span>
                <span>좌석 3</span>
                <span>좌석 4</span> */}
              </div>

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

          {/* <button
            className="action-btn"
            onClick={() => setMsg(`${product.name} 신발 찾기 시작`)}
          >
            신발 찾기
          </button> */}
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
