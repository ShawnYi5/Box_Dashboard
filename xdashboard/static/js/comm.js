window.onerror=function(){return true;};
function updateTips(t) {
	tips
		.text(t)
		.addClass('ui-state-highlight');
	setTimeout(function() {
		tips.removeClass('ui-state-highlight', 1500);
	}, 500);
}
