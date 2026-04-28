import './TryOnModal.css';

type TryOnModalProps = {
  open: boolean;
  onClose: () => void;
  image?: string;
  productName?: string;
  size?: number | null;
  color?: string | null;
  loading?: boolean;
};

export default function TryOnModal({
  open,
  onClose,
  image,
  productName,
  size,
  color,
  loading = false,
}: TryOnModalProps) {
  if (!open) return null;

  return (
    <div className="tryon-modal-overlay">
      <div className="tryon-modal-card">
        <div className="tryon-modal-image-wrap">
          {image ? (
            <img src={image} alt="신발 이미지" className="tryon-modal-image" />
          ) : (
            <div className="tryon-modal-icon">👟</div>
          )}
        </div>

        <div className="tryon-modal-title">
          {loading ? '시착 요청 전달 중' : '시착 요청 접수 완료'}
        </div>

        <div className="tryon-modal-message">
          {loading
            ? '쇼피에게 요청을 전달하고 있어요.'
            : '쇼피가 고객님께서 요청하신 신발을 찾으러 가고 있어요.'}
        </div>

        <div className="tryon-moving-area">
          <div className="tryon-track">
            <div className="tryon-shoppy">🤖</div>
            <div className="tryon-shoe-target">👟</div>
          </div>

          <div className="tryon-progress-text">
            {loading ? '요청 전달 중...' : '쇼피 이동 중...'}
          </div>
        </div>

        <div className="tryon-modal-info-box">
          <div className="tryon-modal-product-name">
            {productName ?? '-'}
          </div>

          <div className="tryon-modal-info-row">
            <span>사이즈</span>
            <strong>{size ?? '-'}</strong>
          </div>

          <div className="tryon-modal-info-row">
            <span>색상</span>
            <strong>{color ?? '-'}</strong>
          </div>
        </div>

        <button
          type="button"
          className="tryon-modal-button"
          onClick={onClose}
          disabled={loading}
        >
          {loading ? '요청 중...' : '확인'}
        </button>
      </div>
    </div>
  );
}