import './ArrivalModal.css';

type Props = {
  open: boolean;
  onClose: () => void;
};

export default function ArrivalModal({ open, onClose }: Props) {
  if (!open) return null;

  return (
    <div className="arrival-overlay">
      <div className="arrival-card">
        <div className="arrival-top-text">
          Shoppy가 상품을 가져왔습니다!
        </div>

        {/* <div className="arrival-map-box">
          <div className="arrival-map-title">로봇 실시간 위치 현황</div>
          <div className="arrival-map-placeholder">- 지도 -</div>
        </div> */}

        <div className="arrival-success-box">
          <div className="arrival-success-title">
            <span className="arrival-check">✔</span>
            도착했습니다!
          </div>

          <div className="arrival-success-desc">
            박스를 가져가신 후 <b>수령</b> 버튼을 눌러주세요.
          </div>

          <button className="arrival-btn" onClick={onClose}>
            수령 완료
          </button>
        </div>
      </div>
    </div>
  );
}