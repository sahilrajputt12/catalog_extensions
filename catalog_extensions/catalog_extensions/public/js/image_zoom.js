// Image Hover Zoom functionality for product detail pages
// Shows magnified view when hovering over product images

function initImageHoverZoom() {
  if (typeof frappe === 'undefined') return;

  frappe.ready(() => {
    // Find all product images on the page
    const productImages = document.querySelectorAll('.product-image, .product-image img, .website-image');
    
    productImages.forEach((imgContainer) => {
      const img = imgContainer.tagName === 'IMG' ? imgContainer : imgContainer.querySelector('img');
      if (!img || !img.src) return;

      // Skip if already initialized
      if (imgContainer.closest('.zoom-container')) return;

      // Create zoom container wrapper
      const zoomContainer = document.createElement('div');
      zoomContainer.className = 'zoom-container';
      
      // Wrap the image container
      imgContainer.parentNode.insertBefore(zoomContainer, imgContainer);
      zoomContainer.appendChild(imgContainer);

      // Create zoom result (magnified view) - shows on the right side
      const zoomResult = document.createElement('div');
      zoomResult.className = 'zoom-result';
      zoomContainer.appendChild(zoomResult);

      // Create zoom lens (follows cursor)
      const zoomLens = document.createElement('div');
      zoomLens.className = 'zoom-lens';
      zoomContainer.appendChild(zoomLens);

      // Calculate zoom ratio
      const cx = zoomResult.offsetWidth / zoomLens.offsetWidth || 2;
      const cy = zoomResult.offsetHeight / zoomLens.offsetHeight || 2;

      // Set background image for zoom result
      zoomResult.style.backgroundImage = `url('${img.src}')`;
      zoomResult.style.backgroundSize = `${img.width * cx}px ${img.height * cy}px`;

      // Mouse enter - show zoom
      zoomContainer.addEventListener('mouseenter', () => {
        zoomResult.style.display = 'block';
        zoomLens.style.display = 'block';
        zoomContainer.classList.add('zoom-active');
      });

      // Mouse leave - hide zoom
      zoomContainer.addEventListener('mouseleave', () => {
        zoomResult.style.display = 'none';
        zoomLens.style.display = 'none';
        zoomContainer.classList.remove('zoom-active');
      });

      // Mouse move - update zoom position
      zoomContainer.addEventListener('mousemove', (e) => {
        e.preventDefault();

        const rect = zoomContainer.getBoundingClientRect();
        let x = e.clientX - rect.left;
        let y = e.clientY - rect.top;

        // Center lens on cursor
        const lensWidth = zoomLens.offsetWidth;
        const lensHeight = zoomLens.offsetHeight;
        
        x = x - lensWidth / 2;
        y = y - lensHeight / 2;

        // Boundary checks
        if (x > rect.width - lensWidth) x = rect.width - lensWidth;
        if (x < 0) x = 0;
        if (y > rect.height - lensHeight) y = rect.height - lensHeight;
        if (y < 0) y = 0;

        // Move lens
        zoomLens.style.left = x + 'px';
        zoomLens.style.top = y + 'px';

        // Move background image in result
        zoomResult.style.backgroundPosition = `-${x * cx}px -${y * cy}px`;
      });

      // Touch support for mobile
      zoomContainer.addEventListener('touchstart', () => {
        zoomResult.style.display = 'block';
        zoomLens.style.display = 'block';
        zoomContainer.classList.add('zoom-active');
      }, { passive: true });

      zoomContainer.addEventListener('touchend', () => {
        zoomResult.style.display = 'none';
        zoomLens.style.display = 'none';
        zoomContainer.classList.remove('zoom-active');
      }, { passive: true });

      zoomContainer.addEventListener('touchmove', (e) => {
        e.preventDefault();
        const touch = e.touches[0];
        const rect = zoomContainer.getBoundingClientRect();
        let x = touch.clientX - rect.left;
        let y = touch.clientY - rect.top;

        const lensWidth = zoomLens.offsetWidth;
        const lensHeight = zoomLens.offsetHeight;
        
        x = x - lensWidth / 2;
        y = y - lensHeight / 2;

        if (x > rect.width - lensWidth) x = rect.width - lensWidth;
        if (x < 0) x = 0;
        if (y > rect.height - lensHeight) y = rect.height - lensHeight;
        if (y < 0) y = 0;

        zoomLens.style.left = x + 'px';
        zoomLens.style.top = y + 'px';
        zoomResult.style.backgroundPosition = `-${x * cx}px -${y * cy}px`;
      }, { passive: false });
    });
  });
}

// Initialize when DOM is ready
if (typeof frappe !== 'undefined' && typeof frappe.ready === 'function') {
  frappe.ready(initImageHoverZoom);
} else {
  document.addEventListener('DOMContentLoaded', initImageHoverZoom);
}

// Also re-init on dynamic content changes
if (typeof frappe !== 'undefined') {
  $(document).on('page-change', () => {
    setTimeout(initImageHoverZoom, 100);
  });
}
