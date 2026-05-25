import { useState } from "react";
import { ImageOff, Download as DownloadIcon } from "lucide-react";

// Phase 20 — ImageBlock
// Props: block = { src, alt, width, height, download_filename }
export default function ImageBlock({ block, msgIdx }) {
  const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
  const fullSrc = block.src?.startsWith("http") ? block.src : `${BACKEND_URL}${block.src}`;
  const [err, setErr] = useState(false);

  if (err) {
    return (
      <div className="smifs-block-image smifs-block-image-error" data-testid={`image-block-${msgIdx}-error`}>
        <ImageOff size={14} /> Couldn't load the generated chart.
      </div>
    );
  }

  return (
    <figure className="smifs-block-image" data-testid={`image-block-${msgIdx}`}>
      <img
        src={fullSrc}
        alt={block.alt || "Generated chart"}
        width={block.width || 1000}
        height={block.height || 700}
        loading="lazy"
        onError={() => setErr(true)}
      />
      <figcaption className="smifs-block-image-cap">
        <span>{block.alt || "Generated chart"}</span>
        <a href={fullSrc} download={block.download_filename || "chart.png"}
            data-testid={`image-block-${msgIdx}-download`}>
          <DownloadIcon size={11} /> Download
        </a>
      </figcaption>
    </figure>
  );
}
