// The app's icons, drawn as in the design: 24-unit strokes in the text color, round caps and joins.
import type { ReactNode, SVGProps } from "react";

type Props = Omit<SVGProps<SVGSVGElement>, "stroke"> & { size?: number; stroke?: number };

function icon(paths: ReactNode, defaults: { stroke?: number; fill?: boolean } = {}) {
  return function Icon({ size = 20, stroke = defaults.stroke ?? 1.75, ...rest }: Props) {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill={defaults.fill ? "currentColor" : "none"}
           stroke={defaults.fill ? "none" : "currentColor"} strokeWidth={stroke} strokeLinecap="round"
           strokeLinejoin="round" aria-hidden="true" focusable="false" {...rest}>
        {paths}
      </svg>
    );
  };
}

export const IconFeed = icon(<>
  <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H12v16H6.5A2.5 2.5 0 0 0 4 21z" />
  <path d="M20 5.5A2.5 2.5 0 0 0 17.5 3H12v16h5.5a2.5 2.5 0 0 1 2.5 2z" />
</>);
export const IconLeaf = icon(<><path d="M5 20c0-8 4-13 14-14-1 10-6 14-14 14z" /><path d="M5 20c3-4 6-7 10-10" /></>);
export const IconFolder = icon(<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />);
export const IconCalendar = icon(<><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M3 10h18M8 3v4M16 3v4" /></>);
export const IconDigest = icon(<><rect x="3" y="5" width="18" height="15" rx="2" /><path d="M7 9h6M7 13h10M7 17h10" /></>);
export const IconChat = icon(<path d="M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-5 4z" />);
export const IconTodo = icon(<path d="M4 7l2 2 4-4M4 15l2 2 4-4M13 7h7M13 15h7" />);
export const IconSettings = icon(<>
  <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
  <circle cx="12" cy="12" r="3" />
</>);
export const IconPlus = icon(<path d="M12 5v14M5 12h14" />, { stroke: 2 });
export const IconSearch = icon(<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>);
export const IconBack = icon(<path d="M19 12H5M11 18l-6-6 6-6" />);
export const IconPin = icon(<path d="M9 3h6l-1 7 3 3v2h-4v6l-1 1-1-1v-6H7v-2l3-3z" />, { stroke: 2 });
export const IconExternal = icon(<path d="M14 4h6v6M20 4l-9 9M18 13v6H5V6h6" />);
export const IconMore = icon(<><circle cx="5" cy="12" r="2" /><circle cx="12" cy="12" r="2" /><circle cx="19" cy="12" r="2" /></>, { fill: true });
export const IconClip = icon(<path d="m21 12-8.5 8.5a5 5 0 0 1-7-7L14 5a3.3 3.3 0 0 1 4.7 4.7L10.5 18a1.7 1.7 0 0 1-2.4-2.4L16 7.7" />);
export const IconReset = icon(<><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5" /></>);
export const IconSend = icon(<path d="M12 19V5M5 12l7-7 7 7" />, { stroke: 2.25 });
export const IconDown = icon(<path d="m6 9 6 6 6-6" />, { stroke: 2 });
export const IconPrev = icon(<path d="m15 6-6 6 6 6" />, { stroke: 2 });
export const IconNext = icon(<path d="m9 6 6 6-6 6" />, { stroke: 2 });
export const IconClose = icon(<path d="M18 6 6 18M6 6l12 12" />, { stroke: 2 });
export const IconEdit = icon(<path d="M4 20h4l10-10-4-4L4 16zM13 7l4 4" />);
export const IconTrash = icon(<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />);
export const IconLock = icon(<><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></>);
export const IconCheck = icon(<path d="M5 12l5 5L20 7" />, { stroke: 2 });
export const IconSpark = icon(<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6" />);
export const IconArchive = icon(<><rect x="3" y="4" width="18" height="5" rx="1.5" /><path d="M5 9v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9M10 13h4" /></>);
export const IconDownload = icon(<path d="M12 4v11M7 10l5 5 5-5M5 20h14" />);
export const IconSpinner = ({ size = 16 }: { size?: number }) => (
  <svg className="spin" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
       strokeLinecap="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.2-8.6" /></svg>
);
