/**
 * Product Image Zoom - Desktop hover + Mobile tap-to-zoom
 * Handles both product detail pages and listing cards
 */

(function() {
    'use strict';

    const zoomLevel = 2.5;
    const zoomWindowSize = 350;
    const isTouchDevice = window.matchMedia('(pointer: coarse)').matches;

    // Initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', startObserver);
    } else {
        startObserver();
    }

    function startObserver() {
        initAllZooms();

        // Watch for dynamically loaded products
        const observer = new MutationObserver(function(mutations) {
            let shouldInit = false;
            mutations.forEach(function(mutation) {
                if (mutation.type === 'childList') {
                    mutation.addedNodes.forEach(function(node) {
                        if (node.nodeType === 1) {
                            if (node.matches && (node.matches('.item-card') || node.matches('[class*="product"]') || node.querySelector('.item-card, .card-img-container, .product-image'))) {
                                shouldInit = true;
                            }
                        }
                    });
                }
            });
            if (shouldInit) {
                setTimeout(initAllZooms, 100);
            }
        });

        observer.observe(document.body, { childList: true, subtree: true });
    }

    function initAllZooms() {
        // Product detail page images
        document.querySelectorAll('.product-image:not([data-zoom-initialized])').forEach(function(container) {
            const img = container.querySelector('.website-image');
            if (img && img.src) {
                if (isTouchDevice) {
                    initMobileZoom(container, img);
                } else {
                    initDesktopZoom(container, img);
                }
                container.setAttribute('data-zoom-initialized', 'true');
            }
        });

        // Product listing card images
        document.querySelectorAll('.card-img-container:not([data-zoom-initialized])').forEach(function(container) {
            const img = container.querySelector('.card-img');
            if (img && img.src) {
                if (isTouchDevice) {
                    initMobileZoom(container, img);
                } else {
                    initDesktopZoom(container, img);
                }
                container.setAttribute('data-zoom-initialized', 'true');
            }
        });
    }

    // Desktop: Hover zoom with lens
    function initDesktopZoom(container, img) {
        const instanceId = 'zoom-' + Math.random().toString(36).substr(2, 9);

        const zoomWindow = document.createElement('div');
        zoomWindow.className = 'product-zoom-window';
        zoomWindow.id = instanceId + '-window';
        zoomWindow.innerHTML = `<img src="${img.src}" alt="Zoom view">`;
        document.body.appendChild(zoomWindow);

        const zoomLens = document.createElement('div');
        zoomLens.className = 'product-zoom-lens';
        zoomLens.id = instanceId + '-lens';
        container.style.position = 'relative';
        container.appendChild(zoomLens);

        container.addEventListener('mouseenter', function(e) {
            const rect = container.getBoundingClientRect();
            let left = rect.right + 15;
            let top = rect.top + window.scrollY;

            if (left + zoomWindowSize > window.innerWidth) {
                left = rect.left - zoomWindowSize - 15;
            }
            if (left < 0) {
                left = rect.left;
                top = rect.bottom + window.scrollY + 15;
            }

            zoomWindow.style.left = left + 'px';
            zoomWindow.style.top = top + 'px';
            zoomWindow.style.display = 'block';
            zoomLens.style.display = 'block';

            const zoomImgEl = zoomWindow.querySelector('img');
            zoomImgEl.style.width = (rect.width * zoomLevel) + 'px';
            zoomImgEl.style.height = (rect.height * zoomLevel) + 'px';

            if (zoomImgEl.src !== img.src) {
                zoomImgEl.src = img.src;
            }

            const lensWidth = rect.width / zoomLevel;
            const lensHeight = rect.height / zoomLevel;
            zoomLens.style.width = lensWidth + 'px';
            zoomLens.style.height = lensHeight + 'px';
        });

        container.addEventListener('mouseleave', function() {
            zoomWindow.style.display = 'none';
            zoomLens.style.display = 'none';
        });

        container.addEventListener('mousemove', function(e) {
            const rect = container.getBoundingClientRect();
            let x = e.clientX - rect.left;
            let y = e.clientY - rect.top;

            const lensWidth = parseFloat(zoomLens.style.width);
            const lensHeight = parseFloat(zoomLens.style.height);

            let lensX = x - lensWidth / 2;
            let lensY = y - lensHeight / 2;

            lensX = Math.max(0, Math.min(lensX, rect.width - lensWidth));
            lensY = Math.max(0, Math.min(lensY, rect.height - lensHeight));

            zoomLens.style.left = lensX + 'px';
            zoomLens.style.top = lensY + 'px';

            const zoomImgEl = zoomWindow.querySelector('img');
            const zoomX = lensX * zoomLevel * -1;
            const zoomY = lensY * zoomLevel * -1;
            zoomImgEl.style.transform = `translate(${zoomX}px, ${zoomY}px)`;
        });

        // Watch for image src changes
        const observer = new MutationObserver(function(mutations) {
            mutations.forEach(function(mutation) {
                if (mutation.type === 'attributes' && mutation.attributeName === 'src') {
                    const zoomImgEl = zoomWindow.querySelector('img');
                    if (zoomImgEl && img.src !== zoomImgEl.src) {
                        zoomImgEl.src = img.src;
                    }
                }
            });
        });
        observer.observe(img, { attributes: true, attributeFilter: ['src'] });
    }

    // Mobile: Tap to open zoom modal with pan
    function initMobileZoom(container, img) {
        container.style.cursor = 'zoom-in';

        container.addEventListener('click', function(e) {
            e.preventDefault();
            openMobileZoomModal(img.src);
        });
    }

    function openMobileZoomModal(imgSrc) {
        // Close existing modal if any
        const existingModal = document.querySelector('.mobile-zoom-modal');
        if (existingModal) {
            existingModal.remove();
            document.body.style.overflow = '';
            return;
        }

        // Create modal
        const modal = document.createElement('div');
        modal.className = 'mobile-zoom-modal';
        modal.innerHTML = `
            <div class="mobile-zoom-backdrop"></div>
            <div class="mobile-zoom-content">
                <div class="mobile-zoom-image-wrapper">
                    <img src="${imgSrc}" alt="Zoomed view" class="mobile-zoom-image">
                </div>
                <button class="mobile-zoom-close">&times;</button>
                <div class="mobile-zoom-hint">Pinch to zoom, drag to pan</div>
            </div>
        `;

        document.body.appendChild(modal);
        document.body.style.overflow = 'hidden';

        const wrapper = modal.querySelector('.mobile-zoom-image-wrapper');
        const zoomImg = modal.querySelector('.mobile-zoom-image');
        const closeBtn = modal.querySelector('.mobile-zoom-close');
        const backdrop = modal.querySelector('.mobile-zoom-backdrop');

        let scale = 2;
        let panX = 0;
        let panY = 0;
        let initialDistance = 0;
        let initialScale = 2;
        let isDragging = false;
        let startX = 0;
        let startY = 0;

        function updateTransform() {
            zoomImg.style.transform = `translate(${panX}px, ${panY}px) scale(${scale})`;
        }

        // Initial center position
        panX = 0;
        panY = 0;
        updateTransform();

        // Close handlers
        closeBtn.addEventListener('click', function() {
            modal.remove();
            document.body.style.overflow = '';
        });

        backdrop.addEventListener('click', function() {
            modal.remove();
            document.body.style.overflow = '';
        });

        // Touch events for pinch and pan
        let touches = [];

        wrapper.addEventListener('touchstart', function(e) {
            e.preventDefault();
            touches = Array.from(e.touches);

            if (touches.length === 2) {
                // Pinch start
                initialDistance = getDistance(touches[0], touches[1]);
                initialScale = scale;
            } else if (touches.length === 1) {
                // Pan start
                isDragging = true;
                startX = touches[0].clientX - panX;
                startY = touches[0].clientY - panY;
            }
        }, { passive: false });

        wrapper.addEventListener('touchmove', function(e) {
            e.preventDefault();
            touches = Array.from(e.touches);

            if (touches.length === 2) {
                // Pinch zoom
                const distance = getDistance(touches[0], touches[1]);
                scale = Math.max(1, Math.min(5, initialScale * (distance / initialDistance)));
                updateTransform();
            } else if (touches.length === 1 && isDragging) {
                // Pan
                panX = touches[0].clientX - startX;
                panY = touches[0].clientY - startY;
                updateTransform();
            }
        }, { passive: false });

        wrapper.addEventListener('touchend', function(e) {
            touches = Array.from(e.touches);
            if (touches.length === 0) {
                isDragging = false;
            }
        });

        // Double tap to toggle zoom
        let lastTap = 0;
        wrapper.addEventListener('touchend', function(e) {
            const now = Date.now();
            if (now - lastTap < 300) {
                // Double tap
                if (scale > 1) {
                    scale = 1;
                    panX = 0;
                    panY = 0;
                } else {
                    scale = 2;
                }
                updateTransform();
            }
            lastTap = now;
        });
    }

    function getDistance(touch1, touch2) {
        const dx = touch1.clientX - touch2.clientX;
        const dy = touch1.clientY - touch2.clientY;
        return Math.sqrt(dx * dx + dy * dy);
    }
})();
