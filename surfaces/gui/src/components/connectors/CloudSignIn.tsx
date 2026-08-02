// Delta intentionally has no product account or managed OAuth broker. Connector
// credentials remain on this machine and are configured through each manual flow.
export function CloudSignInInline({ blurb }: { blurb?: string }) {
  return (
    <div className="delta-connection-note text-[11.5px] text-faint" data-testid="local-connection-note">
      {blurb || "Delta uses local credentials. Choose Manual to connect this service."}
    </div>
  );
}

export function CloudStatusPending() {
  return (
    <div className="delta-connection-note text-[12px] text-faint py-2 text-center" data-testid="local-connection-note">
      Delta uses local credentials. Choose Manual to continue.
    </div>
  );
}
