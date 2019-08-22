var jsArray = [
	"js/jquery-1.12.4.min.js",
	"js/jquery-ui-1.11.4.custom/jquery-ui.min.js",
	"js/comm2.js",
	"js/Guriddo_jqGrid_JS_5.1.1/js/i18n/grid.locale-cn.js",
	"js/Guriddo_jqGrid_JS_5.1.1/js/jquery.jqGrid.min.js",
	"js/jquery.cookie.js",
	"js/jquery.pngFix.js",
	"js/markdown.js"
];

var cssArray = [
	"css/comm.css",
	"js/jquery-ui-1.11.4.custom/jquery-ui.css",
	"js/Guriddo_jqGrid_JS_5.1.1/css/ui.jqgrid.css"
];

var includeJS = function(){
	for (i = 0; i < jsArray.length; i++) {
		document.write("<script type='text/javascript' src='/static/"+jsArray[i]+"'></script>");
	}
}
includeJS();

var includeCSS = function(){
	for (i = 0; i < cssArray.length; i++) {
		document.write('<link  rel="stylesheet" media="screen" type="text/css" href="/static/'+cssArray[i]+'" />');
	}
}
includeCSS();