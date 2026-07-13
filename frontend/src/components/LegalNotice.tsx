type LegalNoticeProps = {
  notice: string;
};

export function LegalNotice({ notice }: LegalNoticeProps) {
  return (
    <div className="legal-notice">
      <div className="legal-notice__label">Authorized Use Notice</div>
      <p>{notice}</p>
    </div>
  );
}

