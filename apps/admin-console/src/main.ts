import { createApp } from 'vue';
import 'element-plus/dist/index.css';
import './theme.css';
import App from './App.vue';

// Legacy links never reach an authentication endpoint or a retained application variable.
if (location.hash) history.replaceState(null, '', location.pathname + location.search);
createApp(App).mount('#app');
