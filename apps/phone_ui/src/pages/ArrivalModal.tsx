// import './ArrivalModal.css';

// type Props = {
//   open: boolean;
//   onClose: () => void;
//   type?: 'success' | 'fail'; // 👈 추가
// };

// // export default function ArrivalModal({ open, onClose }: Props) {
// export default function ArrivalModal({ open, onClose, type = 'success' }: Props) {
//   if (!open) return null;

//   return (
//     <div className="arrival-overlay">
//       <div className="arrival-card">
//         <div className="arrival-top-text">
//           Shoppy가 상품을 가져왔습니다!
//         </div>

//         {/* <div className="arrival-map-box">
//           <div className="arrival-map-title">로봇 실시간 위치 현황</div>
//           <div className="arrival-map-placeholder">- 지도 -</div>
//         </div> */}

//         <div className="arrival-success-box">
//           <div className="arrival-success-title">
//             <span className="arrival-check">✔</span>
//             도착했습니다!
//           </div>

//           <div className="arrival-success-desc">
//             박스를 가져가신 후 <b>수령</b> 버튼을 눌러주세요.
//           </div>

//           <button className="arrival-btn" onClick={onClose}>
//             수령 완료
//           </button>
//         </div>
//       </div>
//     </div>
//   );
// }




import './ArrivalModal.css';

type Props = {
  open: boolean;
  onClose: () => void;
  type?: 'success' | 'fail'; // 👈 추가
};

export default function ArrivalModal({ open, onClose, type = 'success' }: Props) {
  if (!open) return null;

  const isFail = type === 'fail';

  return (
    <div className="arrival-overlay">
      <div className="arrival-card">
        <div className="arrival-top-text">
          {isFail
            ? '시착 요청 처리 중 문제가 발생했습니다.'
            : 'Shoppy가 상품을 가져왔습니다!'}
        </div>

        <div className={`arrival-success-box ${isFail ? 'fail' : ''}`}>
          <div className={`arrival-success-title ${isFail ? 'fail' : ''}`}>
            <span className={`arrival-check ${isFail ? 'fail' : ''}`}>
              {isFail ? '!' : '✔'}
            </span>
            {isFail ? '시착 요청 실패' : '도착했습니다!'}
          </div>

          <div className="arrival-success-desc">
            {isFail
              ? '잠시 후 다시 시도해주세요.'
              : <>박스를 가져가신 후 <b>수령</b> 버튼을 눌러주세요.</>}
          </div>

          <button
            className={`arrival-btn ${isFail ? 'fail' : ''}`}
            onClick={onClose}
          >
            {isFail ? '확인' : '수령 완료'}
          </button>
        </div>
      </div>
    </div>
  );
}