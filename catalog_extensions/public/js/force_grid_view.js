frappe.ready(() => {
  try {
    localStorage.setItem("product_view", "Grid View");
  } catch (e) {
    // ignore storage errors
  }

  const hideList = () => {
    const $listArea = $("#products-list-area");
    const $gridArea = $("#products-grid-area");
    if ($listArea.length) {
      $listArea.addClass("hidden").hide();
    }
    if ($gridArea.length) {
      $gridArea.removeClass("hidden").show();
    }
  };

  hideList();

  $(document).on("click", "#list", function (e) {
    e.preventDefault();
    e.stopImmediatePropagation();
    hideList();
    try {
      localStorage.setItem("product_view", "Grid View");
    } catch (e) {
      // ignore
    }
  });
});
