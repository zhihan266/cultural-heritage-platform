<script lang="ts">
import { defineComponent, h } from 'vue'
import { markdownNodes } from '@/utils/markdown'
export default defineComponent({
  props: { text: { type: String, required: true } },
  setup: (props) => () => h('div', { class: 'markdown' }, markdownNodes(props.text)),
})
</script>
